"""Opt-in inference only: empty summaries are not proof of empty detail data."""
METRICS = {
    'heart_rate': ('minHeartRate','maxHeartRate','restingHeartRate'),
    'resting_hr': ('restingHeartRate','minHeartRate','maxHeartRate'),
    'stress': ('averageStressLevel','maxStressLevel'),
    'steps': ('totalSteps',), 'floors': ('floorsAscended','floorsDescended'),
    'respiration': ('avgWakingRespirationValue','highestRespirationValue','lowestRespirationValue','latestRespirationValue'),
    'spo2': ('averageSpo2','lowestSpo2','latestSpo2'),
    'body_battery_events': ('bodyBatteryHighestValue','bodyBatteryLowestValue','bodyBatteryChargedValue','bodyBatteryDrainedValue'),
}

def explicit_nulls(value,fields):
    return isinstance(value,dict) and all(f in value and value[f] is None for f in fields)

def summary_reason(dataset,summary):
    if dataset not in METRICS or not isinstance(summary,dict):return None
    fields=METRICS[dataset]
    if explicit_nulls(summary,fields):
        return {'rule':'explicit_null_summary_v1','fields':{f:summary[f] for f in fields}}
    return None

def activity_reason(method,activity):
    if not isinstance(activity,dict):return None
    if method=='get_activity_hr_in_timezones' and explicit_nulls(activity,('averageHR','maxHR')):
        return {'rule':'explicit_null_activity_hr_v1','fields':{'averageHR':None,'maxHR':None}}
    if method=='get_activity_weather' and activity.get('hasPolyline') is False and explicit_nulls(activity,('startLatitude','startLongitude')):
        return {'rule':'explicit_null_activity_location_v1','fields':{'hasPolyline':False,'startLatitude':None,'startLongitude':None}}
    return None
