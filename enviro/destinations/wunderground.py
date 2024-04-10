from enviro import logging
from enviro.constants import UPLOAD_SUCCESS, UPLOAD_FAILED
import urequests
import config
from enviro.helpers import celcius_to_fahrenheit, hpa_to_inches, metres_per_second_to_miles_per_hour, mm_to_inches

def log_destination():
  logging.info(f"> uploading cached readings to Weather Underground device: {config.wunderground_id}")

def get_wunderground_timestamp(enviro_timestamp):
  year = enviro_timestamp[0:4]
  month = enviro_timestamp[5:7]
  day = enviro_timestamp[8:10]
  hour = enviro_timestamp[11:13]
  minute = enviro_timestamp[14:16]
  second = enviro_timestamp[17:19]
  timestamp = year + "-" + month+ "-" + day + "+" + hour + "%3A" + minute + "%3A" + second
  return timestamp

# API documentation https://support.weather.com/s/article/PWS-Upload-Protocol?language=en_GB
def upload_reading(reading):
  timestamp = get_wunderground_timestamp(reading["timestamp"])
  
  url = "https://weatherstation.wunderground.com/weatherstation/updateweatherstation.php?ID=" + config.wunderground_id + "&PASSWORD=" + config.wunderground_key + "&dateutc=" + timestamp + "&softwaretype=EnviroWeather&action=updateraw"

  # convert and append applicable readings to URL
  for key, value in reading["readings"].items():
    if key == "temperature":
      url += "&tempf=" + str(celcius_to_fahrenheit(value))

    if key == "humidity":
      # Humidity can exceed 100% but API states 0-100 accepted values
      if value > 100:
        value = 100
      url += "&humidity=" + str(value)
    
    if key == "sea_level_pressure":
      url += "&baromin=" + str(hpa_to_inches(value))
    
    if key == "wind_speed" and "avg_wind_speed" not in reading["readings"]:
      url += "&windspeedmph=" + str(metres_per_second_to_miles_per_hour(value))
    
    if key == "avg_wind_speed":
      url += "&windspeedmph=" + str(metres_per_second_to_miles_per_hour(value))

    if key == "gust_wind_speed":
      url += "&windgustmph=" + str(metres_per_second_to_miles_per_hour(value))

    if key == "wind_direction":
      url += "&winddir=" + str(value)
    
    if key == "rain_per_hour":
      url += "&rainin=" + str(mm_to_inches(value))
    
    if key == "rain_today":
      url += "&dailyrainin=" + str(mm_to_inches(value))

    if key == "dewpoint":
      url += "&dewptf=" + str(celcius_to_fahrenheit(value))

  logging.info(f"> upload url: {url}")

  try:
    # send (GET) reading data to http endpoint
    result = urequests.get(url)

    result.close()
    
    if result.status_code == 200:
      return UPLOAD_SUCCESS

    logging.debug(f"  - upload issue ({result.status_code} {result.reason})")
  except:
    logging.debug(f"  - an exception occurred when uploading")

  return UPLOAD_FAILED