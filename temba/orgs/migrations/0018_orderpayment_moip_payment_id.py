# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0017_orderpayment'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderpayment',
            name='moip_payment_id',
            field=models.CharField(help_text='Payment MoIP identifier', max_length=255, null=True, blank=True),
        ),
    ]
