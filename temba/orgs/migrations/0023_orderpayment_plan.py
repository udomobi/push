# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0022_auto_20160412_2145'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderpayment',
            name='plan',
            field=models.CharField(default='basic', max_length=255, verbose_name='Plan'),
            preserve_default=False,
        ),
    ]
