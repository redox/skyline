import pandas
import numpy as np
import scipy
from scipy import stats
import traceback
import logging
from time import time

from algorithm_exceptions import *

# This is the rolling duration that will be stored in Redis. Be sure to pick a
# value that suits your memory capacity, your CPU capacity, and your overall
# metrics count. Longer durations take a longer to analyze, but they can
# help the algorithms reduce the noise and provide more accurate anomaly
# detection.
FULL_DURATION = 86400

# This is the duration, in seconds, for a metric to become 'stale' and for
# the analyzer to ignore it until new datapoints are added. 'Staleness' means
# that a datapoint has not been added for STALE_PERIOD seconds.
STALE_PERIOD = 500

# This is the minimum length of a timeseries, in datapoints, for the analyzer
# to recognize it as a complete series.
MIN_TOLERABLE_LENGTH = 1

# Sometimes a metric will continually transmit the same number. There's no need
# to analyze metrics that remain boring like this, so this setting determines
# the amount of boring datapoints that will be allowed to accumulate before the
# analyzer skips over the metric. If the metric becomes noisy again, the
# analyzer will stop ignoring it.
MAX_TOLERABLE_BOREDOM = 100

# By default, the analyzer skips a metric if it it has transmitted a single
# number MAX_TOLERABLE_BOREDOM times. Change this setting if you wish the size
# of the ignored set to be higher (ie, ignore the metric if there have only
# been two different values for the past MAX_TOLERABLE_BOREDOM datapoints).
# This is useful for timeseries that often oscillate between two values.
BOREDOM_SET_SIZE = 1

# These are the algorithms that the Analyzer will run. To add a new algorithm,
# you must both define the algorithm in algorithms.py and add its name here.
ALGORITHMS = [
    'first_hour_average',
    'mean_subtraction_cumulation',
    'stddev_from_average',
    'stddev_from_moving_average',
    'least_squares',
    'grubbs',
    'histogram_bins',
    'median_absolute_deviation',
    'ks_test',
]

# This is the number of algorithms that must return True before a metric is
# classified as anomalous.
CONSENSUS = 6

logger = logging.getLogger("AnalyzerLog")

"""
This is no man's land. Do anything you want in here,
as long as you return a boolean that determines whether the input
timeseries is anomalous or not.

To add an algorithm, define it here, and add its name to settings.ALGORITHMS.
"""


def tail_avg(timeseries):
    """
    This is a utility function used to calculate the average of the last three
    datapoints in the series as a measure, instead of just the last datapoint.
    It reduces noise, but it also reduces sensitivity and increases the delay
    to detection.
    """
    try:
        t = (timeseries[-1][1] + timeseries[-2][1] + timeseries[-3][1]) / 3
        return t
    except IndexError:
        return timeseries[-1][1]


def median_absolute_deviation(timeseries):
    """
    A timeseries is anomalous if the deviation of its latest datapoint with
    respect to the median is X times larger than the median of deviations.
    """

    series = pandas.Series([x[1] for x in timeseries])
    median = series.median()
    demedianed = np.abs(series - median)
    median_deviation = demedianed.median()

    # The test statistic is infinite when the median is zero,
    # so it becomes super sensitive. We play it safe and skip when this happens.
    if median_deviation == 0:
        return False

    test_statistic = demedianed.iget(-1) / median_deviation

    # Completely arbitary...triggers if the median deviation is
    # 6 times bigger than the median
    if test_statistic > 6:
        return True


def grubbs(timeseries):
    """
    A timeseries is anomalous if the Z score is greater than the Grubb's score.
    """

    series = scipy.array([x[1] for x in timeseries])
    stdDev = scipy.std(series)
    mean = np.mean(series)
    tail_average = tail_avg(timeseries)
    z_score = (tail_average - mean) / stdDev
    len_series = len(series)
    threshold = scipy.stats.t.isf(.05 / (2 * len_series), len_series - 2)
    threshold_squared = threshold * threshold
    grubbs_score = ((len_series - 1) / np.sqrt(len_series)) * np.sqrt(threshold_squared / (len_series - 2 + threshold_squared))

    return z_score > grubbs_score


def first_hour_average(timeseries):
    """
    Calcuate the simple average over one hour, FULL_DURATION seconds ago.
    A timeseries is anomalous if the average of the last three datapoints
    are outside of three standard deviations of this value.
    """
    last_hour_threshold = time() - (FULL_DURATION - 3600)
    series = pandas.Series([x[1] for x in timeseries if x[0] < last_hour_threshold])
    mean = (series).mean()
    stdDev = (series).std()
    t = tail_avg(timeseries)

    return abs(t - mean) > 3 * stdDev


def stddev_from_average(timeseries):
    """
    A timeseries is anomalous if the absolute value of the average of the latest
    three datapoint minus the moving average is greater than three standard
    deviations of the average. This does not exponentially weight the MA and so
    is better for detecting anomalies with respect to the entire series.
    """
    series = pandas.Series([x[1] for x in timeseries])
    mean = series.mean()
    stdDev = series.std()
    t = tail_avg(timeseries)

    return abs(t - mean) > 3 * stdDev


def stddev_from_moving_average(timeseries):
    """
    A timeseries is anomalous if the absolute value of the average of the latest
    three datapoint minus the moving average is greater than three standard
    deviations of the moving average. This is better for finding anomalies with
    respect to the short term trends.
    """
    series = pandas.Series([x[1] for x in timeseries])
    expAverage = pandas.stats.moments.ewma(series, com=50)
    stdDev = pandas.stats.moments.ewmstd(series, com=50)

    return abs(series.iget(-1) - expAverage.iget(-1)) > 3 * stdDev.iget(-1)


def mean_subtraction_cumulation(timeseries):
    """
    A timeseries is anomalous if the value of the next datapoint in the
    series is farther than three standard deviations out in cumulative terms
    after subtracting the mean from each data point.
    """

    series = pandas.Series([x[1] if x[1] else 0 for x in timeseries])
    series = series - series[0:len(series) - 1].mean()
    stdDev = series[0:len(series) - 1].std()
    expAverage = pandas.stats.moments.ewma(series, com=15)

    return abs(series.iget(-1)) > 3 * stdDev


def least_squares(timeseries):
    """
    A timeseries is anomalous if the average of the last three datapoints
    on a projected least squares model is greater than three sigma.
    """

    x = np.array([t[0] for t in timeseries])
    y = np.array([t[1] for t in timeseries])
    A = np.vstack([x, np.ones(len(x))]).T
    results = np.linalg.lstsq(A, y)
    residual = results[1]
    m, c = np.linalg.lstsq(A, y)[0]
    errors = []
    for i, value in enumerate(y):
        projected = m * x[i] + c
        error = value - projected
        errors.append(error)

    if len(errors) < 3:
        return False

    std_dev = scipy.std(errors)
    t = (errors[-1] + errors[-2] + errors[-3]) / 3

    return abs(t) > std_dev * 3 and round(std_dev) != 0 and round(t) != 0


def histogram_bins(timeseries):
    """
    A timeseries is anomalous if the average of the last three datapoints falls
    into a histogram bin with less than 20 other datapoints (you'll need to tweak
    that number depending on your data)

    Returns: the size of the bin which contains the tail_avg. Smaller bin size
    means more anomalous.
    """

    series = scipy.array([x[1] for x in timeseries])
    t = tail_avg(timeseries)
    h = np.histogram(series, bins=15)
    bins = h[1]
    for index, bin_size in enumerate(h[0]):
        if bin_size <= 20:
            # Is it in the first bin?
            if index == 0:
                if t <= bins[0]:
                    return True
            # Is it in the current bin?
            elif t >= bins[index] and t < bins[index + 1]:
                    return True

    return False


def ks_test(timeseries):
    """
    A timeseries is anomalous if 2 sample Kolmogorov-Smirnov test indicates
    that data distribution for last 10 minutes is different from last hour.
    It produces false positives on non-stationary series so Augmented
    Dickey-Fuller test applied to check for stationarity.
    """

    hour_ago = time() - 3600
    ten_minutes_ago = time() - 600
    reference = scipy.array([x[1] for x in timeseries if x[0] >= hour_ago and x[0] < ten_minutes_ago])
    probe = scipy.array([x[1] for x in timeseries if x[0] >= ten_minutes_ago])

    if reference.size < 20 or probe.size < 20:
        return False

    ks_d, ks_p_value = scipy.stats.ks_2samp(reference, probe)

    if ks_p_value < 0.05 and ks_d > 0.5:
        adf = sm.tsa.stattools.adfuller(reference, 10)
        if adf[1] < 0.05:
            return True

    return False


def run_selected_algorithm(timeseries):
    """
    Filter timeseries and run selected algorithm.
    Return true in case of anomaly.
    """
    # Get rid of short series
    if len(timeseries) < MIN_TOLERABLE_LENGTH:
        raise TooShort()

    # Get rid of stale series
    if time() - timeseries[-1][0] > STALE_PERIOD:
        raise Stale()

    # Get rid of boring series
    if len(set(item[1] for item in timeseries[-MAX_TOLERABLE_BOREDOM:])) == BOREDOM_SET_SIZE:
        raise Boring()

    try:
        ensemble = [globals()[algorithm](timeseries) for algorithm in ALGORITHMS]
        return ensemble.count(True) >= CONSENSUS
    except:
        logging.error("Algorithm error: " + traceback.format_exc())
        return False
