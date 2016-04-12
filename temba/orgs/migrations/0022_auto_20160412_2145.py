# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0021_auto_20160412_2031'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orderpayment',
            name='credits',
            field=models.IntegerField(help_text='The number of credits bought in this top up', verbose_name='Number of Credits'),
        ),
    ]
