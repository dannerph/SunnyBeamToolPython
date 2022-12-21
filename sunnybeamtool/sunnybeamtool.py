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
        checksum = bytearray(crc.to_bytes(length=2, byteorder='little'))
        newcrc = bytearray()
        for value in checksum:
            if value == 0x7e:
                newcrc.append(0x7d) 
                newcrc.append(0x5e) 
            elif value == 0x7d:
                newcrc.append(0x7d) 
                newcrc.append(0x5d) 
            else:
                newcrc.append(value) 
        msg[-3:-1] = newcrc

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

    def calc_checksum(self, msg, ignore_escaping):
        fcs = 0xffff
        escape_next = False
        for b in msg[1:-3]:
            if ~ignore_escaping and b == 0x7d:
                escape_next = True
                continue
            else:
                if escape_next:
                    b ^= 0x20
                    escape_next = False
            tmp = (fcs ^ int(b)) & 0xff
            tmp2 = fcstable[tmp]
            fcs = (int)((fcs >> 8) ^ tmp2)
        return (fcs ^ 0xffff)



# FCS lookup table as calculated by the table generator.
fcstable = [
    0x0000, 0x1189, 0x2312, 0x329b, 0x4624, 0x57ad, 0x6536, 0x74bf,
    0x8c48, 0x9dc1, 0xaf5a, 0xbed3, 0xca6c, 0xdbe5, 0xe97e, 0xf8f7,
    0x1081, 0x0108, 0x3393, 0x221a, 0x56a5, 0x472c, 0x75b7, 0x643e,
    0x9cc9, 0x8d40, 0xbfdb, 0xae52, 0xdaed, 0xcb64, 0xf9ff, 0xe876,
    0x2102, 0x308b, 0x0210, 0x1399, 0x6726, 0x76af, 0x4434, 0x55bd,
    0xad4a, 0xbcc3, 0x8e58, 0x9fd1, 0xeb6e, 0xfae7, 0xc87c, 0xd9f5,
    0x3183, 0x200a, 0x1291, 0x0318, 0x77a7, 0x662e, 0x54b5, 0x453c,
    0xbdcb, 0xac42, 0x9ed9, 0x8f50, 0xfbef, 0xea66, 0xd8fd, 0xc974,
    0x4204, 0x538d, 0x6116, 0x709f, 0x0420, 0x15a9, 0x2732, 0x36bb,
    0xce4c, 0xdfc5, 0xed5e, 0xfcd7, 0x8868, 0x99e1, 0xab7a, 0xbaf3,
    0x5285, 0x430c, 0x7197, 0x601e, 0x14a1, 0x0528, 0x37b3, 0x263a,
    0xdecd, 0xcf44, 0xfddf, 0xec56, 0x98e9, 0x8960, 0xbbfb, 0xaa72,
    0x6306, 0x728f, 0x4014, 0x519d, 0x2522, 0x34ab, 0x0630, 0x17b9,
    0xef4e, 0xfec7, 0xcc5c, 0xddd5, 0xa96a, 0xb8e3, 0x8a78, 0x9bf1,
    0x7387, 0x620e, 0x5095, 0x411c, 0x35a3, 0x242a, 0x16b1, 0x0738,
    0xffcf, 0xee46, 0xdcdd, 0xcd54, 0xb9eb, 0xa862, 0x9af9, 0x8b70,
    0x8408, 0x9581, 0xa71a, 0xb693, 0xc22c, 0xd3a5, 0xe13e, 0xf0b7,
    0x0840, 0x19c9, 0x2b52, 0x3adb, 0x4e64, 0x5fed, 0x6d76, 0x7cff,
    0x9489, 0x8500, 0xb79b, 0xa612, 0xd2ad, 0xc324, 0xf1bf, 0xe036,
    0x18c1, 0x0948, 0x3bd3, 0x2a5a, 0x5ee5, 0x4f6c, 0x7df7, 0x6c7e,
    0xa50a, 0xb483, 0x8618, 0x9791, 0xe32e, 0xf2a7, 0xc03c, 0xd1b5,
    0x2942, 0x38cb, 0x0a50, 0x1bd9, 0x6f66, 0x7eef, 0x4c74, 0x5dfd,
    0xb58b, 0xa402, 0x9699, 0x8710, 0xf3af, 0xe226, 0xd0bd, 0xc134,
    0x39c3, 0x284a, 0x1ad1, 0x0b58, 0x7fe7, 0x6e6e, 0x5cf5, 0x4d7c,
    0xc60c, 0xd785, 0xe51e, 0xf497, 0x8028, 0x91a1, 0xa33a, 0xb2b3,
    0x4a44, 0x5bcd, 0x6956, 0x78df, 0x0c60, 0x1de9, 0x2f72, 0x3efb,
    0xd68d, 0xc704, 0xf59f, 0xe416, 0x90a9, 0x8120, 0xb3bb, 0xa232,
    0x5ac5, 0x4b4c, 0x79d7, 0x685e, 0x1ce1, 0x0d68, 0x3ff3, 0x2e7a,
    0xe70e, 0xf687, 0xc41c, 0xd595, 0xa12a, 0xb0a3, 0x8238, 0x93b1,
    0x6b46, 0x7acf, 0x4854, 0x59dd, 0x2d62, 0x3ceb, 0x0e70, 0x1ff9,
    0xf78f, 0xe606, 0xd49d, 0xc514, 0xb1ab, 0xa022, 0x92b9, 0x8330,
    0x7bc7, 0x6a4e, 0x58d5, 0x495c, 0x3de3, 0x2c6a, 0x1ef1, 0x0f78
]
