# -*- coding: utf-8 -*-
"""语音识别：优先本地 SenseVoice（sherpa-onnx），其次 Whisper，退回 Windows SAPI。

对外接口保持极简，老调用点不用改：

    import stt
    stt.preload(cfg)                 # 可选，提前加载模型（后台线程里调）
    text = stt.recognize_wav(wav, cfg)

引擎选择（cfg["stt_engine"]）：
    auto       —— sensevoice > whisper > sapi，按可用性从优到劣
    sensevoice —— 中文专用非自回归模型，短句 ~0.1s，比 whisper-small 更快更准
    whisper    —— faster-whisper，多语种，但每次都要跑满 30s 编码窗口
    sapi       —— Windows 内置听写（准确率差，仅作兜底）

模型首次使用会下载到 <工程>/models/ 下，之后完全离线。
"""

import os
import re
import sys
import threading
import time
import wave

# HuggingFace 直连在国内基本不通，默认走镜像；模型下完就不再联网
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # 打包后 stt.py 在 PYZ 里，__file__ 指向 _internal；
    # 模型目录要跟 exe 同级，用户把 models/ 放在 exe 旁边就能用。
    BASE_DIR = os.path.dirname(sys.executable)
MODEL_DIR = os.path.join(BASE_DIR, "models")
SAMPLE_RATE = 16000

DEFAULTS = {
    "stt_engine": "auto",
    "stt_model": "small",          # tiny / base / small / medium / large-v3
    "stt_lang": "zh",
    "stt_beam": 5,
    "stt_vad": True,
    "stt_threads": 4,              # SenseVoice 解码线程数（短句 2 就够，留点余量给别的活）
    # 提示词会作为解码偏置（initial_prompt）：写进常用词能显著减少同音错字，
    # 比如 "语音输入" 曾被识别成 "引输"。改成自己的常用词表即可。
    "stt_prompt": "以下是普通话的句子，请用简体中文转写，加上标点符号。"
                  "可能涉及：语音输入、语音识别、遥控器、上位机、录音、配置、按键、喂喂喂。",
}

# Whisper 在静音/噪声段常见的幻觉输出，直接丢掉
_JUNK = (
    "谢谢观看", "谢谢大家", "请不吝点赞", "订阅", "字幕", "字幕由", "amara",
    "中文字幕", "明镜与点点栏目", "非常感谢", "谢谢", "youtube", "www.",
    "请订阅", "打赏", "视频", "本字幕",
)


# ---------------------------------------------------------------- 引擎状态

_lock = threading.Lock()
_model = None
_model_name = ""
_model_error = ""
_load_cost = 0.0
_engine = ""        # "sensevoice" / "whisper" / "sapi" / ""
_fallback = False   # 首选引擎加载失败后，本次会话降级
_sv = None          # sherpa-onnx OfflineRecognizer
_sv_error = ""
_sv_dir = os.path.join(MODEL_DIR, "sense-voice")


def _has_whisper():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _has_sherpa():
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


def _has_sensevoice_files():
    return (os.path.isfile(os.path.join(_sv_dir, "model.int8.onnx"))
            and os.path.isfile(os.path.join(_sv_dir, "tokens.txt")))


def sensevoice_dir():
    """SenseVoice 模型期望所在目录（打包后就是 exe 同级 models/sense-voice）"""
    return _sv_dir


def have_sensevoice_files():
    return _has_sensevoice_files()


HAVE_WHISPER = _has_whisper()
HAVE_SHERPA = _has_sherpa()
HAVE_SENSEVOICE = HAVE_SHERPA and _has_sensevoice_files()

try:
    import pythoncom
    import win32com.client as win32
    from win32com.client import WithEvents
    HAVE_SAPI = True
except ImportError:  # pragma: no cover
    HAVE_SAPI = False


def _opt(cfg, key):
    if cfg and cfg.get(key) not in (None, ""):
        return cfg[key]
    return DEFAULTS[key]


def _sensevoice_ready():
    return HAVE_SHERPA and _has_sensevoice_files()


def want_engine(cfg=None):
    """按配置决定用哪个引擎。"""
    e = str(_opt(cfg, "stt_engine")).lower()
    if e == "sensevoice":
        return "sensevoice"
    if e == "whisper":
        return "whisper"
    if e == "sapi":
        return "sapi"
    # auto：非自回归的中文模型最快，优先；它不可用再退 whisper，最后 sapi
    if not _fallback:
        if _sensevoice_ready():
            return "sensevoice"
        if HAVE_WHISPER:
            return "whisper"
    elif HAVE_WHISPER:
        return "whisper"
    if HAVE_SAPI:
        return "sapi"
    return ""


def available():
    return bool(want_engine())


def engine_name():
    e = _engine or want_engine()
    if e == "sensevoice":
        return "sensevoice"
    if e == "whisper":
        return "whisper-%s" % (_model_name or _opt(None, "stt_model"))
    if e == "sapi":
        return "sapi"
    return "none"


def status():
    return {
        "engine": engine_name(),
        "ready": (_sv is not None) if (_engine == "sensevoice") else (_model is not None),
        "model": "sense-voice" if _engine == "sensevoice" else _model_name,
        "cost": round(_load_cost, 2),
        "error": _sv_error or _model_error,
        "have_whisper": HAVE_WHISPER,
        "have_sensevoice": _sensevoice_ready(),
        "have_sapi": HAVE_SAPI,
    }


# ---------------------------------------------------------------- 模型加载

def _model_path(name):
    """优先用工程内已下好的本地模型，避免每次联网。"""
    cands = [
        os.path.join(MODEL_DIR, "faster-whisper-" + name),
        os.path.join(MODEL_DIR, name),
    ]
    for p in cands:
        if os.path.isfile(os.path.join(p, "model.bin")):
            return p
    return None


def _load_whisper(name):
    global _model, _model_name, _model_error, _load_cost
    if _model is not None and _model_name == name:
        return _model
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        t0 = time.time()
        try:
            from faster_whisper import WhisperModel
            src = _model_path(name) or name
            os.makedirs(MODEL_DIR, exist_ok=True)
            _model = WhisperModel(
                src,
                device="cpu",
                compute_type="int8",
                download_root=MODEL_DIR,
                cpu_threads=max(2, min(8, (os.cpu_count() or 4) - 2)),
            )
            _model_name = name
            _model_error = ""
            _load_cost = time.time() - t0
        except Exception as e:
            _model = None
            _model_error = "%s: %s" % (type(e).__name__, e)
            _load_cost = time.time() - t0
    return _model


def _load_sensevoice(cfg=None):
    """加载 SenseVoice（非自回归中文模型）。"""
    global _sv, _sv_error, _load_cost
    if _sv is not None:
        return _sv
    with _lock:
        if _sv is not None:
            return _sv
        t0 = time.time()
        try:
            import sherpa_onnx
            n = int(_opt(cfg, "stt_threads") or 0)
            if n <= 0:
                n = max(2, min(4, os.cpu_count() or 4))
            lang = str(_opt(cfg, "stt_lang") or "zh")
            _sv = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=os.path.join(_sv_dir, "model.int8.onnx"),
                tokens=os.path.join(_sv_dir, "tokens.txt"),
                num_threads=n,
                use_itn=True,          # 数字/标点规范化，输出更像正常中文
                language="" if lang in ("", "auto") else lang,
                debug=False,
            )
            _sv_error = ""
            _load_cost = time.time() - t0
        except Exception as e:
            _sv = None
            _sv_error = "%s: %s" % (type(e).__name__, e)
            _load_cost = time.time() - t0
    return _sv


def preload(cfg=None):
    """同步加载模型，返回引擎名。放在后台线程里调用，避免卡界面。"""
    global _engine, _fallback
    e = want_engine(cfg)
    _engine = e
    if e == "sensevoice":
        _load_sensevoice(cfg)
        if _sv is None:
            # 首选引擎起不来：auto 模式下本次会话降级到 whisper / sapi
            _fallback = True
            if HAVE_WHISPER:
                _engine = "whisper"
                _load_whisper(str(_opt(cfg, "stt_model")))
            if _engine == "whisper" and _model is None:
                _engine = "sapi" if HAVE_SAPI else ""
    elif e == "whisper":
        _load_whisper(str(_opt(cfg, "stt_model")))
        if _model is None:
            _engine = "sapi" if HAVE_SAPI else ""
            if str(_opt(cfg, "stt_engine")).lower() == "auto":
                _fallback = True
    return engine_name()


# ---------------------------------------------------------------- 音频处理

def _read_wav(path):
    import numpy as np

    with wave.open(path, "rb") as w:
        ch, sw, sr, n = (w.getnchannels(), w.getsampwidth(),
                         w.getframerate(), w.getnframes())
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("不支持的采样位宽: %d" % sw)

    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def _resample(a, sr, target=SAMPLE_RATE):
    import numpy as np
    if sr == target or len(a) == 0:
        return a
    n = int(round(len(a) * target / float(sr)))
    x_old = np.linspace(0.0, 1.0, len(a), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.interp(x_new, x_old, a).astype(np.float32)


def _trim(a, sr, thresh=0.022, pad=0.12):
    """掐掉首尾静音，减少 Whisper 在空白段瞎编。"""
    import numpy as np
    if len(a) == 0:
        return a
    win = max(1, int(sr * 0.01))
    frames = len(a) // win
    if frames < 3:
        return a
    e = np.abs(a[:frames * win]).reshape(frames, win).max(axis=1)
    peak = float(e.max())
    if peak <= 1e-4:
        return a
    idx = np.nonzero(e > max(thresh, peak * 0.06))[0]
    if len(idx) == 0:
        return a
    p = int(sr * pad)
    s = max(0, idx[0] * win - p)
    t = min(len(a), (idx[-1] + 1) * win + p)
    return a[s:t]


def _prepare(path, norm=True):
    """读 WAV → 单声道 16k float32 → 去直流/归一化/剪静音。"""
    import numpy as np
    a, sr = _read_wav(path)
    a = _resample(a, sr)
    if len(a) == 0:
        return a
    a = a - float(a.mean())
    peak = float(np.abs(a).max())
    if peak > 1e-6:
        a = a * (0.89 / peak)          # 遥控器麦克风电平偏低，拉满再识别
    a = _trim(a, SAMPLE_RATE)
    return np.ascontiguousarray(a, dtype=np.float32)


def _norm_cn(t):
    """只留中日韩汉字和字母数字，用来做相似度比较。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t or "")


def _echo_of_prompt(text, prompt):
    """判断这段话是不是 Whisper 把 initial_prompt 原样吐了出来。

    静音段/噪声段的经典幻觉：输出的不是听到的话，而是提示词本身
    （日志里出现过「请用简体中文转写，加上标点符号。可能涉及：语音输入…」）。
    做法：去掉标点后求最长公共子串，够长或占比够高就判定为回声。
    """
    s, p = _norm_cn(text), _norm_cn(prompt)
    if len(s) < 8 or len(p) < 8:
        return False
    # 最长公共子串（长度都在百字节级，DP 足够快）
    prev = [0] * (len(p) + 1)
    best = 0
    for i in range(1, len(s) + 1):
        cur = [0] * (len(p) + 1)
        si = s[i - 1]
        for j in range(1, len(p) + 1):
            if si == p[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best >= 12 or (best >= 8 and best >= len(s) * 0.6)


def _rms(a):
    import numpy as np
    if len(a) == 0:
        return 0.0
    return float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))


def _clean(text):
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    # 去掉纯幻觉段
    low = t.replace(" ", "")
    for k in _JUNK:
        if k and low == k:
            return ""
    # 中文场景下把半角标点换成全角，键入时更像正常中文
    cjk = r"[\u4e00-\u9fff]"
    t = re.sub(r"(%s)\s*,\s*(?=%s)" % (cjk, cjk), "\\1，", t)
    t = re.sub(r"(%s)\?" % cjk, "\\1？", t)
    t = re.sub(r"(%s)!" % cjk, "\\1！", t)
    t = re.sub(r"(%s)\." % cjk, "\\1。", t)
    t = re.sub(r"(%s);" % cjk, "\\1；", t)
    return t.strip(" ，。、,.!")


# ---------------------------------------------------------------- SenseVoice

def _sensevoice_recognize(path, cfg=None):
    """SenseVoice 识别。

    与 Whisper 的关键区别：非自回归，一次前向出结果，计算量只和「真实音频长度」
    成正比。实测 2~3 秒的短句 0.10~0.15s，而 Whisper 无论多短都要跑满 30 秒编码窗。
    """
    rec = _load_sensevoice(cfg)
    if rec is None:
        return ""

    try:
        raw, _sr = _read_wav(path)
        if _rms(raw) < 8e-4:           # 整段几乎没声音，直接跳过
            return ""
    except Exception:
        pass

    audio = _prepare(path)
    if len(audio) < SAMPLE_RATE * 0.25:
        return ""

    stream = rec.create_stream()
    stream.accept_waveform(SAMPLE_RATE, audio)
    rec.decode_stream(stream)
    return _clean(stream.result.text)


# ---------------------------------------------------------------- Whisper

def _whisper_recognize(path, cfg=None):
    model = _load_whisper(str(_opt(cfg, "stt_model")))
    if model is None:
        return ""

    # 整段几乎没声音（比如按键没按稳、音频超时留下的缓冲）就别送了：
    # 白跑一遍模型不说，还容易触发提示词回声幻觉
    try:
        raw, _sr = _read_wav(path)
        if _rms(raw) < 8e-4:           # ≈ -62 dBFS
            return ""
    except Exception:
        pass

    audio = _prepare(path)
    if len(audio) < SAMPLE_RATE * 0.25:
        return ""

    prompt = str(_opt(cfg, "stt_prompt") or "")
    kw = dict(
        language=str(_opt(cfg, "stt_lang")),
        beam_size=max(1, int(_opt(cfg, "stt_beam"))),
        temperature=0.0,
        condition_on_previous_text=False,
        initial_prompt=prompt or None,
        no_speech_threshold=0.65,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )
    if _opt(cfg, "stt_vad"):
        kw["vad_filter"] = True
        kw["vad_parameters"] = {"min_silence_duration_ms": 250,
                                "speech_pad_ms": 120}

    try:
        segments, _info = model.transcribe(audio, **kw)
        parts = [s.text for s in segments]
    except Exception:
        # VAD 的 onnxruntime 出问题时退回不剪静音
        kw.pop("vad_filter", None)
        kw.pop("vad_parameters", None)
        segments, _info = model.transcribe(audio, **kw)
        parts = [s.text for s in segments]

    text = _clean("".join(parts))
    if _echo_of_prompt(text, prompt):
        return ""
    return text


# ---------------------------------------------------------------- SAPI 兜底

class _Collector(object):
    def __init__(self):
        self.parts = []
        self.finished = False

    def OnRecognition(self, *args):
        try:
            res = win32.Dispatch(args[-1])
            self.parts.append(res.PhraseInfo.GetText())
        except Exception:
            pass

    def OnEndStream(self, *args):
        self.finished = True


def _pick_recognizer():
    try:
        cat = win32.Dispatch("SAPI.SpObjectTokenCategory")
        cat.SetId(r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Recognizers", False)
        tokens = cat.EnumerateTokens()
        for i in range(tokens.Count):
            tok = tokens.Item(i)
            if "2052" in tok.Id or "ZH-CN" in tok.Id.upper():
                return tok
        if tokens.Count:
            return tokens.Item(0)
    except Exception:
        pass
    return None


def _sapi_recognize(path, timeout=20.0):
    if not HAVE_SAPI:
        return ""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return ""
    pythoncom.CoInitialize()
    try:
        stream = win32.Dispatch("SAPI.SpFileStream")
        stream.Open(path, 0, False)

        recog = win32.Dispatch("SAPI.SpInProcRecognizer")
        token = _pick_recognizer()
        if token is not None:
            recog.Recognizer = token
        recog.AudioInputStream = stream

        ctx = recog.CreateRecoContext()
        ctx.State = 1
        grammar = ctx.CreateGrammar()
        grammar.DictationSetState(1)

        handler = WithEvents(ctx, _Collector)
        t0 = time.time()
        while not handler.finished and time.time() - t0 < timeout:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.02)
        try:
            stream.Close()
        except Exception:
            pass
        return _clean("".join(handler.parts))
    except Exception:
        return ""
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ---------------------------------------------------------------- 对外入口

def recognize_wav(path, cfg=None):
    """识别一个 WAV 文件，返回文本；失败返回空串。"""
    global _engine, _model_error, _sv_error
    if not path or not os.path.exists(path):
        return ""
    e = want_engine(cfg)
    _engine = e
    if e == "sensevoice":
        try:
            return _sensevoice_recognize(path, cfg)
        except Exception as ex:
            _sv_error = "%s: %s" % (type(ex).__name__, ex)
            return ""
    if e == "whisper":
        try:
            return _whisper_recognize(path, cfg)
        except Exception as ex:
            _model_error = "%s: %s" % (type(ex).__name__, ex)
            return ""
    if e == "sapi":
        return _sapi_recognize(path)
    return ""
