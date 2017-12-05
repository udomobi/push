# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json

from django.db import migrations
from django.db.models import Q


def change_quick_replies_format(ActionSet):
    actionsets = ActionSet.objects.filter(Q(actions__icontains='quick_replies') | Q(actions__icontains='url_buttons')).order_by('created_on')

    for item in actionsets:
        actions_json = json.loads(item.actions)

        for action in actions_json:
            quick_replies = action.get('quick_replies', {})
            url_buttons = action.get('url_buttons', {})

            try:
                quick_replies_keys = quick_replies.keys()
                if len(quick_replies_keys) > 0:
                    print('Migrating Quick Replies on ActionSet %s' % action.get('uuid'))
                    _quick_replies = []
                    for key in quick_replies_keys:
                        for index, resp in enumerate(quick_replies[key]):
                            try:
                                _quick_replies[index].update({key: resp.get('title')})
                            except Exception:
                                _quick_replies.insert(index, {key: resp.get('title')})

                    action['quick_replies'] = _quick_replies
            except Exception as e:
                print('Skipping Quick Replies on ActionSet: %s' % e.args)

            try:
                url_buttons_keys = url_buttons.keys()
                if len(url_buttons_keys) > 0:
                    print('Migrating URL Buttons on ActionSet %s' % action.get('uuid'))
                    _url_buttons = []
                    for key in url_buttons_keys:
                        for index, resp in enumerate(url_buttons[key]):
                            try:
                                _url_buttons[index].update({key: {'title': resp.get('title'),
                                                                  'url': resp.get('url')}})
                            except Exception:
                                _url_buttons.insert(index, {key: {'title': resp.get('title'),
                                                                  'url': resp.get('url')}})

                    action['url_buttons'] = _url_buttons
            except Exception as e:
                print('Skipping URL Buttons on ActionSet: %s' % e.args)

        actions_json = json.dumps(actions_json)
        item.actions = actions_json
        item.save()


def apply_manual():
    from temba.flows.models import ActionSet
    change_quick_replies_format(ActionSet)


def apply_as_migration(apps, schema_editor):
    ActionSet = apps.get_model('flows', 'ActionSet')
    change_quick_replies_format(ActionSet)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0127_backfill_flowrun_path'),
    ]

    operations = [
        migrations.RunPython(apply_as_migration)
    ]
