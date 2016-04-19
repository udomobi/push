# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0025_auto_20160418_1748'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderpayment',
            name='billing_agreement_id',
            field=models.CharField(help_text='PayPal billing agreement ID', max_length=255, null=True, verbose_name='Billing Agreement ID'),
        ),
        migrations.AlterField(
            model_name='orderpayment',
            name='transaction_id',
            field=models.CharField(help_text='PayPal request transaction ID', max_length=255, verbose_name='Transaction ID'),
        ),
    ]
