from usb import core, util
import crcmod
import logging
import asyncio
import struct
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

class SunnyBeam:

    def __init__(self):
        self.__CRCFUN = crcmod.predefined.mkCrcFun('x-25')
        self.__connected = False

    async def connect(self):

        # find SMA device
        dev = core.find(idVendor=0x1587, idProduct=0x002D)
        if dev is None:
            _LOGGER.critical('Sunny Beam not found.')
        else:
            # Reset device and activate first available configuration
            dev.reset()
            dev.set_configuration()
            self.__dev = dev

            _LOGGER.info("Device Manufacturer: " + self.__dev.manufacturer)
            _LOGGER.info("Serial Number: " + self.__dev.serial_number)

            util.claim_interface(self.__dev, 0)
            
             # First do a SET_FEATURE config
            if dev.ctrl_transfer(bmRequestType=0x40, bRequest=0x03, wIndex=0x0000, wValue=0x4138) == 0:

                # Fetching device ID
                self.__device_id = await self.__search_device_id()
                if self.__device_id == None:
                    _LOGGER.error("Could not fetch device ID. Further request will not work")
                else:
                    _LOGGER.debug("device id= " + hex(self.__device_id[1]).lstrip("0x") + hex(self.__device_id[0]).lstrip("0x"))
                    self.__connected = True


    async def get_measurements(self):

        if not self.__connected or not await self.__do_syn_online():
            _LOGGER.error("Sunny Beam not available.")
            return 0

        cmd_get_data = bytearray([0x7e, 0xff, 0x03, 0x40, 0x41, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x0b, 0x0f, 0x09, 0x00, 0x00, 0x00, 0x7e])
        resu = await self.__send_raw_message(cmd_get_data, True)
        if resu > 0:
            buf = await self.__read_raw_message(50)
            if len(buf) <= 0:
                return 0

            pac = int(struct.unpack('f', buf[25:29])[0])
            etoday = round(struct.unpack('f', buf[29:33])[0], 3)
            etotal = round(struct.unpack('f', buf[33:37])[0], 3)
            
            _LOGGER.info("pac: " + str(pac) + " W")
            _LOGGER.info("e-today: " + str(etoday) + " kWh")
            _LOGGER.info("e-total: " + str(etotal) + " kWh")

        return (pac, etoday, etotal)

    async def get_today_measurements(self):

        if not self.__connected:
            _LOGGER.error("Sunny Beam not available.")
            return 0

        cmd_get_data = bytearray([0x7e, 0xff, 0x03, 0x40, 0x41, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x0b, 0x04, 0x19, 0x01, 0xd1, 0x4c, 0x20, 0x4a, 0xff, 0xff, 0xff, 0x7f, 0x00, 0x00, 0x7e])

        data = await self.__do_combined_read_messages(cmd_get_data)
        return self.__parse_measurements(rawdata=data)


    async def get_last_month_measurements(self):

        if not self.__connected:
            _LOGGER.error("Sunny Beam not available.")
            return 0

        cmd_get_data = bytearray([0x7e, 0xff, 0x03, 0x40, 0x41, 0x00, 0x00, 0xd4, 0xf5, 0x10, 0x00, 0x0b, 0x04, 0x7d, 0x31, 0x02, 0x7f, 0x25, 0x1f, 0x4a, 0xff, 0xff, 0xff, 0x7f, 0x00, 0x00, 0x7e])

        data = await self.__do_combined_read_messages(cmd_get_data)
        return self.__parse_measurements(rawdata=data)


    async def __do_combined_read_messages(self, input_msg):
        if not await self.__do_syn_online():
            return 0
        
        # first message
        if await self.__send_raw_message(input_msg, True) == 0:
            return 0
        
        buf_out = bytearray()
        min = 20
        linecnt = 0xFF
        while (linecnt != 0):
            min -= 1
            if (min == 0):
                break
            if (linecnt != 0xFF):
                # ask next messages
                cmd_get_data = bytearray([0x7e, 0xff, 0x03, 0x40, 0x41, 0x00, 0x00, 0x00, 0x00, 0x10, linecnt, 0x0b, 0x00, 0x00, 0x7e])
                resu = await self.__send_raw_message(cmd_get_data, True)
                if resu == 0:
                    return 0
            tmpbuf = await self.__read_raw_message(50)
            if len(tmpbuf) <= 0:
                return buf_out
            if len(tmpbuf) > 12:
                buf_out.extend(tmpbuf[12:-3]) # remove first 12 bytes and last 3 bytes (CRC + 7e
            
            linecnt = tmpbuf[10]

        _LOGGER.debug("Read multiple msgs: " + buf_out.hex())
        return buf_out


    def __parse_measurements(self, rawdata):
        if len(rawdata) <= 0:
            return 0

        data = []
        for i in range(5,len(rawdata), 12):
            part_buf = rawdata[i:i+12]
            _LOGGER.debug("day: " + part_buf.hex())

            val = round(struct.unpack('f', part_buf[8:])[0], 0)
            timestamp = struct.unpack('i', part_buf[0:4])[0]
            time = datetime.utcfromtimestamp(timestamp)
            data.append((time, val))

        return list(reversed(data))


    async def __send_raw_message(self, msg: bytearray, set_deviceid: bool):
        if set_deviceid:
            msg[7:9] = self.__device_id

        msg_for_crc = bytearray()
        escape_next = False
        for b in msg[1:-3]:
            if b == 0x7d:
                escape_next = True
            else:
                if escape_next:
                    b ^= 0x20
                    escape_next = False
                msg_for_crc.append(b)
        
        # Add CRC
        crc = self.__CRCFUN(msg_for_crc)
        msg[-3:-1] = bytearray(crc.to_bytes(length=2, byteorder='little'))
        _LOGGER.debug("Sent: " + msg.hex())
        
        await asyncio.sleep(0.2)

        return self.__dev.write(endpoint=0x02, data=msg, timeout=1000)


    async def __read_raw_message(self, max_iterations: int, buffer_size: int=1024):
        buf_out = bytearray()
        # reading can spawn multiple 'usb_bulk_read operations
        # always ignore the first two raw bytes and seek for "0x7e...0x7e sequence
        start_found = False
        previous_char_is_escape = False

        await asyncio.sleep(0.3)

        for _ in range(max_iterations):
            await asyncio.sleep(0.07)

            buf_in = bytearray(self.__dev.read(0x81, buffer_size, 1000).tobytes())
            _LOGGER.debug("raw_read: " + buf_in.hex())

            # Process payload if available
            if len(buf_in) > 2 :
                end_found = False

                for p in range(2, len(buf_in)):
                    myByte = buf_in[p]

                    if myByte == 0x7e:
                        if not start_found:
                            start_found = True
                        else:
                            end_found = True

                    # long communications get a 0x01 0x60 in between. not sure why...
                    if (start_found and (myByte == 0x60) and (buf_in[p - 1] == 0x01)):
                        del buf_out[-1]
                        continue

                    if myByte == 0x7d:
                        previous_char_is_escape = True
                        continue

                    if previous_char_is_escape:
                        if myByte == 0x5e:
                            myByte = 0x7e # not end!
                        elif myByte == 0x5d:
                            myByte = 0x7d # not and escape char!
                        else:
                            myByte ^= 0x20
                        previous_char_is_escape = False

                    buf_out.append(myByte)
                    if end_found:
                        break #stop payload processing

                if end_found:
                    break # stop iterations

        _LOGGER.debug("raw_read processed: " + buf_out.hex())
            
        # Check CRC
        if len(buf_out) > 2:
            crc = bytearray(self.__CRCFUN(buf_out[1:-3]).to_bytes(length=2, byteorder='little'))
            returned_crc = buf_out[-3:-1]
            if crc != returned_crc:
                _LOGGER.warning("read bad crc " + returned_crc.hex() + ", should be " + crc.hex() + ". Message *should* be rejected.")
        return buf_out


    async def __search_device_id(self):
        basic_msg = bytearray([0x7e, 0xFF, 0x03, 0x40, 0x41, 0x00, 0x00, 0x00, 0x00, 0x90, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7e])

        # Integrate serial number in request
        serial_number_prepared = int(self.__dev.serial_number) + 140000000
        basic_msg[12:16] = bytearray(serial_number_prepared.to_bytes(length=4, byteorder='little'))

        resu = await self.__send_raw_message(basic_msg, False)
        if resu > 0:
            data = await self.__read_raw_message(20)
            if len(data) < 7:
                return None
            else:
                return data[5:7]
        return None


    async def __do_syn_online(self):
        cmd_syn_online = bytearray([0x7e, 0xff, 0x03, 0x40, 0x41, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x00, 0x2d, 0x2e, 0x7e])
        resu = await self.__send_raw_message(cmd_syn_online, False)
        if resu == 0:
            return False
        # always read dummy data
        await self.__read_raw_message(5)
        return True
