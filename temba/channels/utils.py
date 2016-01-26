from django.http.response import HttpResponse

__author__ = 'teehamaral'
import json


def get_carriers(request):
    from carrier import Carrier
    c = Carrier()
    carriers = []
    for c in json.loads(c.get_search(str('iso'), str(request.GET.get('country')))):
        carrier_dict = {}
        carrier_dict['id'] = c['mnc']
        carrier_dict['text'] = "{0} - {1}".format(c['network'], c['mnc'])
        carriers.append(carrier_dict)

    return HttpResponse(json.dumps(carriers), 'application/json')
