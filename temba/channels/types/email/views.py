# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView

from temba.contacts.models import EMAIL_SCHEME

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        pop_hostname = forms.CharField(max_length=500, min_length=1, label=_("POP3"), required=True,
                                       help_text=_("Hostname to receive email, like: pop.gmail.com"))

        smtp_hostname = forms.CharField(max_length=500, min_length=1, label=_("SMTP"), required=True,
                                        help_text=_("Hostname to send email, like: smtp.gmail.com"))

        use_ssl = forms.BooleanField(label=_("Use SSL"), initial=True)

        pop_port = forms.IntegerField(label=_("POP Port"), initial=995)

        smtp_port = forms.IntegerField(label=_("SMTP Port"), initial=465)

        username = forms.CharField(max_length=100, label=_("Username"), required=True)

        password = forms.CharField(max_length=100, label=_("Password"), required=True)

        email_from = forms.CharField(max_length=255, label=_("Email from"), required=False)

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        data = form.cleaned_data

        config = {
            'EMAIL_POP_HOSTNAME': data['pop_hostname'],
            'EMAIL_SMTP_HOSTNAME': data['smtp_hostname'],
            'EMAIL_USE_SSL': data['use_ssl'],
            'EMAIL_POP_PORT': data['pop_port'],
            'EMAIL_SMTP_PORT': data['smtp_port'],
            'EMAIL_USERNAME': data['username'],
            'EMAIL_PASSWORD': data['password'],
            'EMAIL_FROM': data['email_from'],
        }

        self.object = Channel.create(org, self.request.user, None, self.channel_type, name="Email: %s" % data['email_from'], address=None, config=config, schemes=[EMAIL_SCHEME])

        return super(ClaimView, self).form_valid(form)
