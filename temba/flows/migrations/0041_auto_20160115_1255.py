# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0040_auto_20151103_1014'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowrevision',
            name='created_by',
            field=models.ForeignKey(related_name='flows_flowrevision_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='flow',
            field=models.ForeignKey(related_name='revisions', to='flows.Flow'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='modified_by',
            field=models.ForeignKey(related_name='flows_flowrevision_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='revision',
            field=models.IntegerField(help_text='Revision number for this definition', null=True),
        ),
    ]
