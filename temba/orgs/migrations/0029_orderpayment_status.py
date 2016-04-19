# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0028_auto_20160418_1848'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderpayment',
            name='status',
            field=models.CharField(default='Active', max_length=255, verbose_name='Status'),
            preserve_default=False,
        ),
    ]
