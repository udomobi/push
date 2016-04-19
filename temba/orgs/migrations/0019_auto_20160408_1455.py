# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0018_orderpayment_moip_payment_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderpayment',
            name='moip_payment_status',
            field=models.CharField(max_length=255, null=True, verbose_name='Status MoIP payment', blank=True),
        ),
        migrations.AlterField(
            model_name='orderpayment',
            name='moip_order_id',
            field=models.CharField(max_length=255, verbose_name='Order MoIP identifier'),
        ),
        migrations.AlterField(
            model_name='orderpayment',
            name='moip_payment_id',
            field=models.CharField(max_length=255, null=True, verbose_name='Payment MoIP identifier', blank=True),
        ),
    ]
