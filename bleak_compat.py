"""bleak 0.22.x（Windows / WinRT backend）两处卡死的修复。

1) get_services() 把 get_gatt_services_async 调用了两次（第一次只包了个
   FutureLike 就丢掉、从不 await），同一设备上并发两次服务枚举会互锁，
   实测调用永不返回。

2) connect() 结尾是 `await event.wait()`，等 GattSessionStatus 变成 ACTIVE
   的事件回调。但遥控器本来就被 Windows 连着，session 建好时已经是 ACTIVE，
   状态不会再"变化"，事件永远不触发 —— 这就是界面一直停在“连接中”的原因。
   改成就地读 session.session_status 判断。

3) disconnect() 同样在等 session 关闭事件，一并绕开。

补丁只替换 WinRT backend 的方法；其它平台或版本导入失败会自动跳过。
"""

import logging

log = logging.getLogger(__name__)

_applied = False


def apply():
    """给 bleak 的 WinRT backend 打补丁，返回是否成功。"""
    global _applied
    if _applied:
        return True
    try:
        from bleak.backends.service import BleakGATTServiceCollection
        from bleak.backends.winrt import client as wc
        from bleak.backends.winrt.characteristic import BleakGATTCharacteristicWinRT
        from bleak.backends.winrt.descriptor import BleakGATTDescriptorWinRT
        from bleak.backends.winrt.service import BleakGATTServiceWinRT
    except Exception as e:  # pragma: no cover - 非 Windows / 其它 backend
        log.debug("bleak 补丁跳过：%s", e)
        return False

    BleakClientWinRT = wc.BleakClientWinRT
    GattSession = wc.GattSession
    GattSessionStatus = wc.GattSessionStatus
    GattCommunicationStatus = wc.GattCommunicationStatus
    BleakError = wc.BleakError
    _ensure_success = wc._ensure_success

    async def get_services(self, *, service_cache_mode=None, cache_mode=None, **kwargs):
        if self.services is not None:
            return self.services

        new_services = BleakGATTServiceCollection()
        srv_args = [] if service_cache_mode is None else [service_cache_mode]
        args = [] if cache_mode is None else [cache_mode]

        services = _ensure_success(
            await self._requester.get_gatt_services_async(*srv_args),
            "services", "Could not get GATT services")

        for service in services:
            result = await service.get_characteristics_async(*args)
            if result.status == GattCommunicationStatus.ACCESS_DENIED:
                # HID / Bond Manager 这类被系统独占的服务读不到，跳过
                continue
            characteristics = _ensure_success(
                result, "characteristics",
                "Could not get GATT characteristics for service %s" % service.uuid)

            new_services.add_service(BleakGATTServiceWinRT(service))
            for ch in characteristics:
                descriptors = _ensure_success(
                    await ch.get_descriptors_async(*args), "descriptors",
                    "Could not get GATT descriptors for characteristic %s" % ch.uuid)
                new_services.add_characteristic(BleakGATTCharacteristicWinRT(
                    ch, lambda: self._session.max_pdu_size - 3))
                for d in descriptors:
                    new_services.add_descriptor(BleakGATTDescriptorWinRT(
                        d, str(ch.uuid), ch.attribute_handle))
        return new_services

    async def connect(self, **kwargs):
        if self._device_info is None:
            raise BleakError(
                "%s: 缺少设备地址，无法连接（需要先取得 bluetooth_address）" % self.address)

        self._requester = await self._create_requester(self._device_info)
        try:
            self._session = await GattSession.from_device_id_async(
                self._requester.bluetooth_device_id)
            if not self._session.can_maintain_connection:
                raise BleakError("device does not support GATT sessions")
            self._session.maintain_connection = True

            self.services = await self.get_services()

            # 关键：不再等事件，直接看当前状态
            if self._session.session_status != GattSessionStatus.ACTIVE:
                raise BleakError(
                    "GATT session 未激活：%r" % self._session.session_status)
            return True
        except BaseException:
            for obj in (self._session, self._requester):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:
                    pass
            self._session = None
            self._requester = None
            raise

    async def disconnect(self):
        services = self.services
        for handle, token in list(self._notification_callbacks.items()):
            try:
                char = services.get_characteristic(handle) if services else None
                if char is not None:
                    char.obj.remove_value_changed(token)
            except Exception:
                pass
        self._notification_callbacks.clear()

        if services is not None:
            for svc in services.services.values():
                try:
                    svc.obj.close()
                except Exception:
                    pass
            self.services = None

        if self._session is not None:
            try:
                self._session.maintain_connection = False
            except Exception:
                pass
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

        if self._requester is not None:
            try:
                self._requester.close()
            except Exception:
                pass
            self._requester = None
        return True

    BleakClientWinRT.get_services = get_services
    BleakClientWinRT.connect = connect
    BleakClientWinRT.disconnect = disconnect

    _applied = True
    log.debug("bleak WinRT backend 补丁已应用")
    return True
