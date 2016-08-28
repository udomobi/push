# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contacts', '0042_merge'),
        ('orgs', '0034_merge'),
        ('channels', '0039_auto_20160827_2104'),
        ('msgs', '0059_indexes_update'),
    ]

    operations = [
        migrations.CreateModel(
            name='Call',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text='When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this item was last modified', auto_now=True)),
                ('time', models.DateTimeField(help_text='When this call took place', verbose_name='Time')),
                ('duration', models.IntegerField(default=0, help_text='The duration of this call in seconds, if appropriate', verbose_name='Duration')),
                ('call_type', models.CharField(help_text='The type of call', max_length=16, verbose_name='Call Type', choices=[('unk', 'Unknown Call Type'), ('mo_call', 'Incoming Call'), ('mo_miss', 'Missed Incoming Call'), ('mt_call', 'Outgoing Call'), ('mt_miss', 'Missed Outgoing Call')])),
                ('channel', models.ForeignKey(verbose_name='Channel', to='channels.Channel', help_text='The channel where this call took place', null=True)),
                ('contact', models.ForeignKey(related_name='calls', verbose_name='Contact', to='contacts.Contact', help_text='The phone number for this call')),
                ('created_by', models.ForeignKey(related_name='msgs_call_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='msgs_call_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item')),
                ('org', models.ForeignKey(verbose_name='Org', to='orgs.Org', help_text='The org this call is connected to')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
