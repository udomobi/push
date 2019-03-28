# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-05-22 18:51
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("flows", "0160_backfill_recent_step_uuids")]

    operations = [
        migrations.RemoveField(model_name="flowstep", name="broadcasts"),
        migrations.RemoveField(model_name="flowstep", name="contact"),
        migrations.RemoveField(model_name="flowstep", name="messages"),
        migrations.RemoveField(model_name="flowstep", name="run"),
        migrations.AlterField(
            model_name="actionset", name="destination_type", field=models.CharField(max_length=1, null=True)
        ),
        migrations.DeleteModel(name="FlowStep"),
    ]
