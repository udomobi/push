# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2017-02-28 08:37
from __future__ import absolute_import, division, print_function, unicode_literals

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0082_baseexporttask_2'),
    ]

    operations = [
        migrations.AlterField(
            model_name='exportmessagestask',
            name='created_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was originally created'),
        ),
        migrations.AlterField(
            model_name='exportmessagestask',
            name='modified_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was last modified'),
        ),
        migrations.AlterField(
            model_name='label',
            name='created_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was originally created'),
        ),
        migrations.AlterField(
            model_name='label',
            name='modified_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was last modified'),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='created_on',
            field=models.DateTimeField(blank=True, db_index=True, default=django.utils.timezone.now, editable=False,
                                       help_text='When this broadcast was created'),
        ),
    ]
