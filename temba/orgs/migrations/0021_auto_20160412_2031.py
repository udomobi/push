# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0020_auto_20160412_1637'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orderpayment',
            name='credits',
            field=models.FloatField(help_text='The number of credits bought in this top up', verbose_name='Number of Credits'),
        ),
        migrations.AlterField(
            model_name='orderpayment',
            name='value',
            field=models.FloatField(help_text='The value in cents of the MoIP order', verbose_name='Value'),
        ),
    ]
