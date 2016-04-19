# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0023_orderpayment_plan'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderpayment',
            name='signature',
            field=models.CharField(default='1', max_length=255, verbose_name='Signature'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orderpayment',
            name='transaction_id',
            field=models.CharField(default='1', help_text='PayPal transaction ID', max_length=255, verbose_name='Transaction ID'),
            preserve_default=False,
        ),
    ]
