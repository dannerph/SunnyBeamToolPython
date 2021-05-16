from sunnybeamtool.sunnybeamtool import SunnyBeam
import logging
import asyncio

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)

async def main():
    s_beam = SunnyBeam()
    await s_beam.connect()

    while True:
        data = await s_beam.get_measurements()
        print(data)
        await asyncio.sleep(10)

        # data = await s_beam.get_today_measurements()
        # print(data)

        # data = await s_beam.get_last_month_measurements()
        # print(data)


if __name__ == "__main__":

    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()