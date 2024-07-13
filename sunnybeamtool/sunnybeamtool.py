"""Sunnybeam main class"""

import asyncio
import logging
import struct
from datetime import datetime

import crcmod
from usb import core, util

_LOGGER = logging.getLogger(__name__)

from .const import (
    BASIC_MSG,
    GET_LAST_MONTH_CMD,
    GET_MEASUREMENTS_CMD,
    GET_TODAY_CMD,
    NEXT_MSG_CMD,
    SYN_ONLINE_CMD,
)


class SunnyBeam:
    """Sunny Beam connection"""

    def __init__(self):
        self._crc_function = crcmod.predefined.mkCrcFun("x-25")
        self._dev = None
        self._device_id: bytearray | None = None
        self._connected = False

    async def get_product(self) -> str:
        """Get device serial number

        Returns:
            str: serial number as string
        """
        await self._connect()
        return self._dev.product

    async def get_manufacturer(self) -> str:
        """Get device serial number

        Returns:
            str: serial number as string
        """
        await self._connect()
        return self._dev.manufacturer

    async def get_serial_number(self) -> str:
        """Get device serial number

        Returns:
            str: serial number as string
        """
        await self._connect()
        return self._dev.serial_number

    async def get_measurements(self) -> dict[str, any]:
        """Get measurements from the Sunny Beam device

        Raises:
            ConnectionError: raised if connection is not available or device does not respond

        Returns:
            dict[str, any]: Returns power, energy_today and energy_total measurements in a dict
        """
        await self._connect()
        try:
            await self._send_raw_message(GET_MEASUREMENTS_CMD, True)

            buf = await self._read_raw_message(50)
            if len(buf) <= 0:
                raise ConnectionError("Device does not respond")

            power = int(struct.unpack("f", buf[25:29])[0])
            energy_today = round(struct.unpack("f", buf[29:33])[0], 3)
            energy_total = round(struct.unpack("f", buf[33:37])[0], 3)

            _LOGGER.debug("pac: %d W", power)
            _LOGGER.debug("e-today: %f kWh", energy_today)
            _LOGGER.debug("e-total: %f kWh", energy_total)
            return {
                "power": round(power / 1000.0, 3),
                "energy_today": energy_today,
                "energy_total": energy_total,
            }
        except ConnectionError as err:
            self._connected = False
            raise err

    async def get_today_measurements(self) -> list[tuple[datetime, float]] | None:
        """Get power values of today as list including datetimes

        Returns:
            list[tuple[datetime, float]] | None: power values from today
        """
        await self._connect()
        try:
            data = await self._do_combined_read_messages(GET_TODAY_CMD)
            return self._parse_measurements(rawdata=data)
        except ConnectionError as err:
            self._connected = False
            raise err

    async def get_last_month_measurements(self) -> list[tuple[datetime, float]] | None:
        """Get energy values from last month including datetimes

        Returns:
            list[tuple[datetime, float]] | None: energy values from last month
        """
        await self._connect()
        try:
            data = await self._do_combined_read_messages(GET_LAST_MONTH_CMD)
            return self._parse_measurements(rawdata=data)
        except ConnectionError as err:
            self._connected = False
            raise err

    async def _connect(self) -> None:
        """Try to connect to Sunny Beam USB device

        Raises:
            ConnectionError: raised if connection is not available or device does not respond
        """

        if self._connected:
            return  # Already connected

        # Find SMA device
        loop = asyncio.get_event_loop()
        dev = await loop.run_in_executor(
            None, lambda: core.find(idVendor=0x1587, idProduct=0x002D)
        )
        if dev is None:
            raise ConnectionError("Sunny Beam not found")

        # Reset device and activate first available configuration
        await loop.run_in_executor(None, dev.reset)
        await loop.run_in_executor(None, dev.set_configuration)
        self._dev = dev
        _LOGGER.info(
            "Connected to %s from %s with serial number %s",
            self._dev.product,
            self._dev.manufacturer,
            self._dev.serial_number,
        )
        await loop.run_in_executor(None, lambda: util.claim_interface(self._dev, 0))

        # First do a SET_FEATURE config
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._dev.ctrl_transfer(
                    bmRequestType=0x40, bRequest=0x03, wIndex=0x0000, wValue=0x4138
                ),
            )
            if response != 0:
                raise ConnectionError("Could not set required features to device")
        except core.USBError as err:
            raise ConnectionError("Could not set required features to device") from err

        # Fetching device ID
        if self._device_id is None:
            self._device_id = await self._search_device_id()
            _LOGGER.debug(
                "device id= "
                + hex(self._device_id[1]).lstrip("0x")
                + hex(self._device_id[0]).lstrip("0x")
            )
        await asyncio.sleep(0.7)
        self._connected = True

    async def _do_combined_read_messages(self, input_msg: bytearray) -> bytearray:
        await self._do_syn_online()

        # first message
        await self._send_raw_message(input_msg, True)

        buf_out = bytearray()
        minimum = 20
        linecnt = 0xFF
        while linecnt != 0:
            minimum -= 1
            if minimum == 0:
                break
            if linecnt != 0xFF:
                # ask next messages
                NEXT_MSG_CMD[10] = linecnt
                await self._send_raw_message(NEXT_MSG_CMD, True)
            tmpbuf = await self._read_raw_message(50)
            if len(tmpbuf) <= 0:
                return buf_out

            if len(tmpbuf) > 12:
                # remove first 12 bytes and last 3 bytes (CRC + 7e)
                buf_out.extend(tmpbuf[12:-3])
            linecnt = tmpbuf[10]

        _LOGGER.debug("Read multiple msgs: %s", buf_out.hex())
        return buf_out

    def _parse_measurements(
        self, rawdata: bytearray | None
    ) -> list[tuple[datetime, float]] | None:
        """Parse list measurements

        Args:
            rawdata (bytearray | None): raw data list

        Returns:
            list[tuple[datetime, float]] | None: output list of data after parsing datetime extracting the values. None if error in parsing
        """

        if rawdata is None or len(rawdata) <= 0:
            return None

        data = []
        for i in range(5, len(rawdata), 12):
            part_buf = rawdata[i : i + 12]
            _LOGGER.debug("day: %s", part_buf.hex())

            if len(part_buf) != 12:
                return None
            val = round(struct.unpack("f", part_buf[8:])[0], 0)
            timestamp = struct.unpack("i", part_buf[0:4])[0]
            time = datetime.fromtimestamp(timestamp)
            data.append((time, val))

        return list(reversed(data))

    async def _send_raw_message(self, msg: bytearray, set_device_id: bool) -> int:
        """Sends a raw message and returns the number of bytes written

        Args:
            msg (bytearray): _description_
            set_device_id (bool): _description_

        Raises:
            ConnectionError: raised if connection is not available or device does not respond

        Returns:
            int: Number of bytes written
        """
        if set_device_id:
            msg[7:9] = self._device_id

        msg_for_crc = bytearray()
        escape_next = False
        for b in msg[1:-3]:
            if b == 0x7D:
                escape_next = True
            else:
                if escape_next:
                    b ^= 0x20
                    escape_next = False
                msg_for_crc.append(b)

        # Add CRC
        crc = self._crc_function(msg_for_crc)
        checksum = bytearray(crc.to_bytes(length=2, byteorder="little"))
        new_crc = bytearray()
        for value in checksum:
            if value == 0x7E:
                new_crc.append(0x7D)
                new_crc.append(0x5E)
            elif value == 0x7D:
                new_crc.append(0x7D)
                new_crc.append(0x5D)
            else:
                new_crc.append(value)
        msg[-3:-1] = new_crc

        _LOGGER.debug("Sent: %s", msg.hex())
        await asyncio.sleep(0.2)

        try:
            loop = asyncio.get_event_loop()
            nr_sent_bytes = await loop.run_in_executor(
                None, lambda: self._dev.write(endpoint=0x02, data=msg, timeout=1000)
            )
        except core.USBError as err:
            raise ConnectionError("Device not available") from err
        if nr_sent_bytes <= 0:
            raise ConnectionError("Could not send raw message to device")
        return nr_sent_bytes

    async def _read_raw_message(
        self, max_iterations: int, buffer_size: int = 1024
    ) -> bytearray:
        """Read raw message from device

        Args:
            max_iterations (int): number of iterations to wait for a response (each 70ms)
            buffer_size (int, optional): buffer size. Defaults to 1024.

        Returns:
            bytearray: raw response message
        """
        buf_out = bytearray()
        # reading can spawn multiple 'usb_bulk_read operations
        # always ignore the first two raw bytes and seek for "0x7e...0x7e sequence
        start_found = False
        previous_char_is_escape = False

        await asyncio.sleep(0.3)

        for _ in range(max_iterations):
            await asyncio.sleep(0.07)
            try:
                loop = asyncio.get_event_loop()
                raw_response = await loop.run_in_executor(
                    None,
                    lambda: self._dev.read(
                        endpoint=0x81, size_or_buffer=buffer_size, timeout=1000
                    ),
                )
            except core.USBError as err:
                raise ConnectionError("Could not read form device") from err
            buf_in = bytearray(raw_response.tobytes())
            _LOGGER.debug("raw_read: %s", buf_in.hex())

            # Process payload if available
            if len(buf_in) > 2:
                end_found = False

                for p in range(2, len(buf_in)):
                    my_byte = buf_in[p]

                    if my_byte == 0x7E:
                        if not start_found:
                            start_found = True
                        else:
                            end_found = True

                    # long communications get a 0x01 0x60 in between. not sure why...
                    if start_found and (my_byte == 0x60) and (buf_in[p - 1] == 0x01):
                        del buf_out[-1]
                        continue

                    if my_byte == 0x7D:
                        previous_char_is_escape = True
                        continue

                    if previous_char_is_escape:
                        if my_byte == 0x5E:
                            my_byte = 0x7E  # not end!
                        elif my_byte == 0x5D:
                            my_byte = 0x7D  # not and escape char!
                        else:
                            my_byte ^= 0x20
                        previous_char_is_escape = False

                    buf_out.append(my_byte)
                    if end_found:
                        break  # stop payload processing

                if end_found:
                    break  # stop iterations

        _LOGGER.debug("raw_read processed: %s", buf_out.hex())

        # Check CRC
        if len(buf_out) > 2:
            crc = bytearray(
                self._crc_function(buf_out[1:-3]).to_bytes(length=2, byteorder="little")
            )
            msg_crc = buf_out[-3:-1]
            if crc != msg_crc:
                _LOGGER.warning(
                    "Read bad crc %s, should be %s. Message *should* be rejected",
                    msg_crc.hex(),
                    crc.hex(),
                )
        return buf_out

    async def _search_device_id(self) -> bytearray:
        """Try to find Sunny Beam device id

        Raises:
            ConnectionError: raised if connection is not available or device does not respond

        Returns:
            bytearray: device id as bytearray or None if not found
        """

        # Integrate serial number in request
        serial_number_prepared = int(self._dev.serial_number) + 140000000
        BASIC_MSG[12:16] = bytearray(
            serial_number_prepared.to_bytes(length=4, byteorder="little")
        )

        await self._send_raw_message(BASIC_MSG, False)
        data = await self._read_raw_message(20)
        if len(data) < 7:
            raise ConnectionError("Device does not respond")
        return data[5:7]

    async def _do_syn_online(self):
        """Check if device is still online, if not ConnectionError is raised

        Raises:
            ConnectionError: raised if connection is not available or device does not respond
        """
        await self._send_raw_message(SYN_ONLINE_CMD, False)
        await self._read_raw_message(5)  # always read dummy data
