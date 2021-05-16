from sunnybeamtool.sunnybeamtool import SunnyBeam
import logging


if __name__ == "__main__":

    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)

    s_beam = SunnyBeam()

    #while True:
    data = s_beam.get_measurements()
    print(data)
    #    time.sleep(10)

    # data = s_beam.get_today_measurements()
    # print(data)

    # data = s_beam.get_last_month_measurements()
    # print(data)