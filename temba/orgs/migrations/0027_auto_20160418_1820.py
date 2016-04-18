# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0026_auto_20160418_1803'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orderpayment',
            name='transaction_id',
            field=models.CharField(help_text='PayPal request transaction ID', unique=True, max_length=255, verbose_name='Transaction ID'),
        ),
    ]
