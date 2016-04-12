# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0019_auto_20160408_1455'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='orderpayment',
            name='moip_order_id',
        ),
        migrations.RemoveField(
            model_name='orderpayment',
            name='moip_payment_id',
        ),
        migrations.RemoveField(
            model_name='orderpayment',
            name='moip_payment_status',
        ),
    ]
