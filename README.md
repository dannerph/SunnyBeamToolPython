# SunnyBeamToolPython

Python library for accessing the Sunny Beam device from SMA. Reads power production of the solar panels through the Sunny Beam:

- ```get_measurements()``` current production (power in W, daily energy in kWh, total energy in kWh)
- ```get_today_measurements()``` historical power of the current day (datetime, power in W)
- ```get_last_month_measurements()``` historical energy per day of the last month (datetime, energy in kWh)

Code is based on <https://github.com/SBFspot/SunnyBeamTool> and ported from C to python. Credits go to michael  <mich dot peeters at gmail dot com> and Stefan Arts, Holland <stuffie at steunopensource dot nl>
