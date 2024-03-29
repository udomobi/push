# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-04-18 18:07
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0076_update_dart_ext_urns'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contactfield',
            name='value_type',
            field=models.CharField(choices=[('T', 'Text'), ('N', 'Number'), ('D', 'Date & Time'), ('S', 'State'), ('I', 'District'), ('W', 'Ward')], default='T', max_length=1, verbose_name='Field Type'),
        ),
    ]
