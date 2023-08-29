import time, math, os, config
from breakout_bme280 import BreakoutBME280
from breakout_ltr559 import BreakoutLTR559
from machine import Pin, PWM
from pimoroni import Analog
from enviro import i2c, activity_led, config
import enviro.helpers as helpers
from phew import logging
from enviro.constants import WAKE_REASON_RTC_ALARM, WAKE_REASON_BUTTON_PRESS

# amount of rain required for the bucket to tip in mm
RAIN_MM_PER_TICK = 0.2794

# distance from the centre of the anemometer to the centre 
# of one of the cups in cm
WIND_CM_RADIUS = 7.0
# scaling factor for wind speed in m/s
WIND_FACTOR = 0.0218

bme280 = BreakoutBME280(i2c, 0x77)
ltr559 = BreakoutLTR559(i2c)

wind_direction_pin = Analog(26)
wind_speed_pin = Pin(9, Pin.IN, Pin.PULL_UP)
rain_pin = Pin(10, Pin.IN, Pin.PULL_DOWN)
last_rain_trigger = False

def log_rain():
  # read the current rain entries
  rain_entries = []
  if helpers.file_exists("rain.txt"):
    with open("rain.txt", "r") as rainfile:
      rain_entries = rainfile.read().split("\n")

  # add new entry
  logging.info(f"> add new rain trigger at {helpers.datetime_string()}")
  rain_entries.append(helpers.datetime_string())

  # limit number of entries to 190 - each entry is 21 bytes including
  # newline so this keeps the total rain.txt filesize just under one
  # filesystem block (4096 bytes)
  if len(rain_entries) > 190:
    logging.info("Rain log file exceeded 190 entries and was truncated")
    rain_entries = rain_entries[-190:]

  # write out adjusted rain log
  with open("rain.txt", "w") as rainfile:
    rainfile.write("\n".join(rain_entries))

def startup(reason):
  global last_rain_trigger
  import wakeup

  # check if rain sensor triggered wake
  rain_sensor_trigger = wakeup.get_gpio_state() & (1 << 10)

  if rain_sensor_trigger:
    log_rain()

    last_rain_trigger = True

    # if we were woken by the RTC or a Poke continue with the startup
    return (reason is WAKE_REASON_RTC_ALARM 
      or reason is WAKE_REASON_BUTTON_PRESS)

  # there was no rain trigger so continue with the startup
  return True

def check_trigger():
  global last_rain_trigger
  rain_sensor_trigger = rain_pin.value()

  if rain_sensor_trigger and not last_rain_trigger:
    activity_led(100)
    time.sleep(0.05)
    activity_led(0)

    log_rain()

  last_rain_trigger = rain_sensor_trigger

def wind_speed(sample_time_ms=1000):
  # get initial sensor state
  state = wind_speed_pin.value()

  # create an array for each sensor to log the times when the sensor state changed
  # then we can use those values to calculate an average tick time for each sensor
  ticks = []

  start = time.ticks_ms()
  while time.ticks_diff(time.ticks_ms(), start) <= sample_time_ms:
    now = wind_speed_pin.value()
    if now != state: # sensor output changed
      # record the time of the change and update the state
      ticks.append(time.ticks_ms())
      state = now

  # if no sensor connected then we have no readings, skip
  if len(ticks) < 2:
    return 0

  # calculate the average tick between transitions in ms
  average_tick_ms = (time.ticks_diff(ticks[-1], ticks[0])) / (len(ticks) - 1)

  if average_tick_ms == 0:
    return 0
  # work out rotation speed in hz (two ticks per rotation)
  rotation_hz = (1000 / average_tick_ms) / 2

  # calculate the wind speed in metres per second
  circumference = WIND_CM_RADIUS * 2.0 * math.pi
  wind_m_s = rotation_hz * circumference * WIND_FACTOR

  return wind_m_s

def wind_direction():
  # adc reading voltage to cardinal direction taken from our python
  # library - each array index represents a 22.5 degree step around
  # the compass (index 0 == 0, 1 == 22.5, 2 == 45, etc.)
  # we find the closest matching value in the array and use the index
  # to determine the heading
  ADC_TO_DEGREES = (2.533, 1.308, 1.487, 0.270, 0.300, 0.212, 0.595, 0.408,
                    0.926, 0.789, 2.031, 1.932, 3.046, 2.667, 2.859, 2.265)

  closest_index = -1
  last_index = None

  # ensure we have two readings that match in a row as otherwise if
  # you read during transition between two values it can glitch
  # fixes https://github.com/pimoroni/enviro/issues/20
  voltage = 0.0
  
  value = wind_direction_pin.read_voltage()

  closest_index = -1
  closest_value = float('inf')

  for i in range(16):
    distance = abs(ADC_TO_DEGREES[i] - value)
    if distance < closest_value:
      closest_value = distance
      closest_index = i

  resistance = (voltage * 10000) / (3.3 - voltage)
  logging.info(f"> wind direction stats - voltage: {value}, resistance: {resistance}, closest value: {closest_value}, closest index: {closest_index}")

  wind_direction = closest_index * 22.5

  offset_wind_direction = (wind_direction + 360 + config.wind_direction_offset) % 360
  
  return offset_wind_direction

def rainfall(seconds_since_last):
  new_rain_entries = []
  amount = 0 # rain since last reading
  per_hour = 0
  today = 0
  offset = 0 # UTC offset hours
  
  # configure offset variable for UK BST or timezone offset from config file
  # and BST lookup function
  if config.uk_bst == True:
    if helpers.uk_bst():
      offset = 1
  elif config.utc_offset != 0:
    offset += config.utc_offset

  # determine current day number and timestamp
  now = helpers.timestamp(helpers.datetime_string())
  now_day = helpers.timestamp_day(helpers.datetime_string(), offset)
  logging.info(f"> current day number is {now_day}")
  
  # process the rain file data
  if helpers.file_exists("rain.txt"):
    with open("rain.txt", "r") as rainfile:
      rain_entries = rainfile.read().split("\n")

    # populate latest, per second, today and last hour readings from rain log
    # file, write new rain log file dropping any yesterday readings
    for entry in rain_entries:
      if entry:
        ts = helpers.timestamp(entry)
        tsday = helpers.timestamp_day(entry, config.utc_offset)
        logging.info(f"> rain reading day number is {tsday}")
        # populate amount with rain since the last reading
        if now - ts < seconds_since_last:
          amount += RAIN_MM_PER_TICK
          # add any rain ticks from yesterday since the previous reading
          # this will misallocate day totals, but will ensure the hourly total
          # is correct without introducing complexity backdating yesterday and
          # the error will be minimised with frequent readings
          # TODO sum yesterday rain and generate a rain_today reading with
          # 23:59:59 timestamp of yesterday
          if tsday != now_day:
            today += RAIN_MM_PER_TICK
        # count how many rain ticks in the last hour
        if now - ts < 3600:
          per_hour += RAIN_MM_PER_TICK
        # count how many rain ticks today and drop older entries for new file
        if tsday == now_day:
          today += RAIN_MM_PER_TICK
          new_rain_entries.append(entry)
    
    # write out new adjusted rain log
    with open("rain.txt", "w") as newrainfile:
      newrainfile.write("\n".join(new_rain_entries))
  
  per_second = 0
  if seconds_since_last > 0:
    per_second = amount / seconds_since_last

  return amount, per_second, per_hour, today

def get_sensor_readings(seconds_since_last, is_usb_power):
  # bme280 returns the register contents immediately and then starts a new reading
  # we want the current reading so do a dummy read to discard register contents first
  bme280.read()
  time.sleep(0.1)
  bme280_data = bme280.read()

  ltr_data = ltr559.get_reading()
  rain, rain_per_second, rain_per_hour, rain_today = rainfall(seconds_since_last)

  pressure = bme280_data[1] / 100.0
  temperature = bme280_data[0]
  humidity = bme280_data[2]

  # Compensate for additional heating when on different power sources - this
  # also changes the relative humidity value
  logging.info(f"  - recorded temperature: {temperature}")
  logging.info(f"  - recorded humidity: {humidity}")
  if is_usb_power:
    adjusted_temperature = temperature - config.usb_power_temperature_offset
    logging.info(f"  - USB temperature offset: {config.usb_power_temperature_offset}")
  else:
    adjusted_temperature = temperature - config.battery_power_temperature_offset
    logging.info(f"  - Battery temperature offset: {config.battery_power_temperature_offset}")
  absolute_humidity = helpers.relative_to_absolute_humidity(humidity, temperature)
  humidity = helpers.absolute_to_relative_humidity(absolute_humidity, adjusted_temperature)
  temperature = adjusted_temperature
  logging.info(f"  - adjusted temperature: {temperature}")
  logging.info(f"  - adjusted humidity: {humidity}")

  from ucollections import OrderedDict
  readings = OrderedDict({
    "temperature": round(temperature, 2),
    "pressure": round(pressure, 2),
    "humidity": round(humidity, 2),
    "luminance": round(ltr_data[BreakoutLTR559.LUX], 2),
    "wind_speed": wind_speed(),
    "rain": rain,
    "rain_per_second": rain_per_second,
    "rain_per_hour": rain_per_hour,
    "rain_today": rain_today,
    "wind_direction": wind_direction(),
    "dewpoint": round(helpers.calculate_dewpoint(temperature, humidity), 2)
  })

  # Add adjusted pressure to calculated sea level value if set to in config
  if config.sea_level_pressure:
    logging.info(f"  - recorded temperature: {temperature}")
    logging.info(f"  - recorded pressure: {pressure}")
    sea_level_pressure = round(helpers.get_sea_level_pressure(pressure, temperature, config.height_above_sea_level), 2)
    logging.info(f"  - calculated mean sea level pressure: {sea_level_pressure}")
    readings["sea_level_pressure"] = round(sea_level_pressure, 2)

  return readings