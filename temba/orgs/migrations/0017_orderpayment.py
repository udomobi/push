# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0016_remove_squash'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderPayment',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text='When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this item was last modified', auto_now=True)),
                ('value', models.IntegerField(help_text='The value in cents of the MoIP order', verbose_name='Value')),
                ('credits', models.IntegerField(help_text='The number of credits bought in this top up', verbose_name='Number of Credits')),
                ('moip_order_id', models.CharField(help_text='Order MoIP identifier', max_length=255)),
                ('created_by', models.ForeignKey(related_name='orgs_orderpayment_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='orgs_orderpayment_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item')),
                ('org', models.ForeignKey(related_name='payments', to='orgs.Org', help_text='The organization that payment was requested')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
