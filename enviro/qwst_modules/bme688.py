from breakout_bme68x import BreakoutBME68X
from ucollections import OrderedDict

def get_readings(i2c, address):
    bme688 = BreakoutBME68X(i2c)
    bme688_data = bme688.read()    

    return OrderedDict({
        "temperature_bme688": round(bme688_data[0], 2),
        "humidity_bme688": round(bme688_data[2], 2),
        "pressure_bme688": round(bme688_data[1] / 100.0, 2),
        "gas_resistance_bme688": round(bme688_data[3], 2)
    })