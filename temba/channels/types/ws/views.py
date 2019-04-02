# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from temba.contacts.models import WS_SCHEME
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        name = forms.CharField(label=_("WebSite Name"), help_text=_("The WebSite Name"))

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        self.object = Channel.create(
            org,
            self.request.user,
            None,
            "WS",
            name=self.form.cleaned_data["name"],
            address=settings.WEBSOCKET_ADDRESS,
            schemes=[WS_SCHEME],
        )

        return super(ClaimView, self).form_valid(form)
