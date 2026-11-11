"""Windows 自带 SAPI 的离线语音识别（零额外依赖，pywin32 即可）。

用法：
    import stt
    text = stt.recognize_wav("a.wav")

依赖系统已安装中文语音识别引擎（本机有 MS-2052-80-DESK）。
"""

import os
import time

try:
    import pythoncom
    import win32com.client as win32
    from win32com.client import WithEvents

    HAVE_SAPI = True
except ImportError:  # pragma: no cover - 没装 pywin32 时静默降级
    HAVE_SAPI = False


class _Collector(object):
    """接收 SAPI 识别事件，把分句结果拼起来。"""

    def __init__(self):
        self.parts = []
        self.finished = False

    def OnRecognition(self, *args):
        # 参数为 (StreamNumber, StreamPosition, RecognitionType, Result)
        try:
            res = win32.Dispatch(args[-1])
            self.parts.append(res.PhraseInfo.GetText())
        except Exception:
            pass

    def OnEndStream(self, *args):
        self.finished = True


def _pick_recognizer():
    """优先挑中文（LCID 2052）听写引擎，挑不到就用第一个。"""
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


def recognize_wav(path, timeout=20.0):
    """识别一个 WAV 文件，返回文本；失败返回空串。"""
    if not HAVE_SAPI:
        return ""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return ""

    pythoncom.CoInitialize()
    try:
        stream = win32.Dispatch("SAPI.SpFileStream")
        # 0 = SSFMOpenForRead
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

        # 必须传类，win32com 会实例化并接管事件；传实例会 metaclass 冲突
        handler = WithEvents(ctx, _Collector)

        t0 = time.time()
        while not handler.finished and time.time() - t0 < timeout:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.02)

        try:
            stream.Close()
        except Exception:
            pass
        return "".join(handler.parts).strip()
    except Exception:
        return ""
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def available():
    return HAVE_SAPI
