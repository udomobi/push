# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import base64
import copy
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

import six
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.template import loader
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils import timezone
from django.utils.encoding import force_text, force_bytes
from django_redis import get_redis_connection
from mock import patch, ANY
from six.moves.urllib.parse import quote
from smartmin.tests import SmartminTest

from temba.api.models import WebHookEvent
from temba.channels.views import channel_status_processor
from temba.contacts.models import Contact, ContactGroup, ContactURN, URN, TEL_SCHEME, TWITTER_SCHEME
from temba.flows.models import FlowRun
from temba.ivr.models import IVRCall
from temba.msgs.models import Broadcast, Msg, IVR, WIRED, FAILED, SENT, DELIVERED, ERRORED, INCOMING, PENDING, QUEUED, \
    HANDLER_QUEUE, HANDLE_EVENT_TASK
from temba.msgs.models import SystemLabel
from temba.orgs.models import Org, ALL_EVENTS, ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID, NEXMO_KEY, NEXMO_SECRET, \
    FREE_PLAN, NEXMO_UUID, \
    NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY
from temba.tests import TembaTest, MockResponse, ESMockWithScroll
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct, get_anonymous_user
from temba.utils.dates import datetime_to_ms, ms_to_datetime
from temba.utils.queues import push_task
from twython import TwythonError
from .models import Channel, ChannelCount, ChannelEvent, SyncEvent, Alert, ChannelLog, ChannelSession, CHANNEL_EVENT
from .tasks import check_channels_task, squash_channelcounts


class ChannelTest(TembaTest):

    def setUp(self):
        super(ChannelTest, self).setUp()

        self.channel.delete()

        self.tel_channel = Channel.create(self.org, self.user, 'RW', 'A', name="Test Channel", address="+250785551212",
                                          role="SR", secret="12345", gcm_id="123")

        self.twitter_channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel",
                                              address="billy_bob", role="SR")

        self.unclaimed_channel = Channel.create(None, self.user, None, 'NX', name="Unclaimed Channel", address=None,
                                                secret=None, gcm_id="000")

        self.ussd_channel = Channel.create(self.org, self.user, None, 'JNU', name="Junebug USSD",
                                           address="*123#", role=Channel.ROLE_USSD)

    def send_message(self, numbers, message, org=None, user=None):
        if not org:
            org = self.org

        if not user:
            user = self.user

        group = ContactGroup.get_or_create(org, user, 'Numbers: %s' % ','.join(numbers))
        contacts = list()
        for number in numbers:
            contact, urn_obj = Contact.get_or_create(org, URN.from_tel(number), user=user, name=None)
            contacts.append(contact)

        group.contacts.add(*contacts)

        broadcast = Broadcast.create(org, user, message, [group])
        broadcast.send()

        msg = Msg.objects.filter(broadcast=broadcast).order_by('text', 'pk')
        if len(numbers) == 1:
            return msg.first()
        else:
            return list(msg)

    def assertHasCommand(self, cmd_name, response):
        self.assertEqual(200, response.status_code)
        data = response.json()

        for cmd in data['cmds']:
            if cmd['cmd'] == cmd_name:
                return

        raise Exception("Did not find '%s' cmd in response: '%s'" % (cmd_name, response.content))

    def test_expressions_context(self):
        context = self.tel_channel.build_expressions_context()
        self.assertEqual(context['__default__'], '+250 785 551 212')
        self.assertEqual(context['name'], 'Test Channel')
        self.assertEqual(context['address'], '+250 785 551 212')
        self.assertEqual(context['tel'], '+250 785 551 212')
        self.assertEqual(context['tel_e164'], '+250785551212')

        context = self.twitter_channel.build_expressions_context()
        self.assertEqual(context['__default__'], '@billy_bob')
        self.assertEqual(context['name'], 'Twitter Channel')
        self.assertEqual(context['address'], '@billy_bob')
        self.assertEqual(context['tel'], '')
        self.assertEqual(context['tel_e164'], '')

        context = self.unclaimed_channel.build_expressions_context()
        self.assertEqual(context['__default__'], 'Unclaimed Channel')
        self.assertEqual(context['name'], 'Unclaimed Channel')
        self.assertEqual(context['address'], '')
        self.assertEqual(context['tel'], '')
        self.assertEqual(context['tel_e164'], '')

    def test_deactivate(self):
        self.login(self.admin)
        self.tel_channel.is_active = False
        self.tel_channel.save()
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertEqual(404, response.status_code)

    def test_channellog_links(self):
        self.login(self.admin)

        channel_types = (
            ('JN', Channel.DEFAULT_ROLE, 'Channel Log'),
            ('JNU', Channel.ROLE_USSD, 'USSD Log'),
            ('T', Channel.ROLE_CALL, 'Call Log'),
            ('T', Channel.ROLE_SEND + Channel.ROLE_CALL, 'Channel Log')
        )

        for channel_type, channel_role, link_text in channel_types:
            channel = Channel.create(self.org, self.user, None, channel_type, name="Test Channel", role=channel_role)
            response = self.client.get(reverse('channels.channel_read', args=[channel.uuid]))
            self.assertContains(response, link_text)

    def test_delegate_channels(self):

        self.login(self.admin)

        # we don't support IVR yet
        self.assertFalse(self.org.supports_ivr())

        # pretend we are connected to twiliko
        self.org.config = dict(ACCOUNT_SID='AccountSid', ACCOUNT_TOKEN='AccountToken', APPLICATION_SID='AppSid')
        self.org.save()

        # add a delegate caller
        post_data = dict(channel=self.tel_channel.pk, connection='T')
        response = self.client.post(reverse('channels.channel_create_caller'), post_data)

        # now we should be IVR capable
        self.assertTrue(self.org.supports_ivr())

        # should now have the option to disable
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertContains(response, 'Disable Voice Calls')

        # try adding a caller for an invalid channel
        response = self.client.post('%s?channel=20000' % reverse('channels.channel_create_caller'))
        self.assertEqual(200, response.status_code)
        self.assertEqual('Sorry, a caller cannot be added for that number', response.context['form'].errors['channel'][0])

        # disable our twilio connection
        self.org.remove_twilio_account(self.admin)
        self.assertFalse(self.org.supports_ivr())

        # we should lose our caller
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertNotContains(response, 'Disable Voice Calls')

        # now try and add it back without a twilio connection
        response = self.client.post(reverse('channels.channel_create_caller'), post_data)

        # shouldn't have added, so no ivr yet
        self.assertFalse(self.assertFalse(self.org.supports_ivr()))

        self.assertEqual('A connection to a Twilio account is required', response.context['form'].errors['connection'][0])

    def test_get_channel_type_name(self):
        self.assertEqual(self.tel_channel.get_channel_type_name(), "Android Phone")
        self.assertEqual(self.twitter_channel.get_channel_type_name(), "Twitter Channel")
        self.assertEqual(self.unclaimed_channel.get_channel_type_name(), "Nexmo Channel")

    def test_channel_selection(self):
        # make our default tel channel MTN
        mtn = self.tel_channel
        mtn.name = "MTN"
        mtn.save()

        # create a channel for Tigo too
        tigo = Channel.create(self.org, self.user, 'RW', 'A', "Tigo", "+250725551212", secret="11111", gcm_id="456")

        # new contact on MTN should send with the MTN channel
        msg = self.send_message(['+250788382382'], "Sent to an MTN number")
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(mtn, msg.channel)

        # new contact on Tigo should send with the Tigo channel
        msg = self.send_message(['+250728382382'], "Sent to a Tigo number")
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(tigo, msg.channel)

        # now our MTN contact texts, the tigo number which should change their affinity
        msg = Msg.create_incoming(tigo, "tel:+250788382382", "Send an inbound message to Tigo")
        self.assertEqual(tigo, msg.channel)
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(tigo, ContactURN.objects.get(path='+250788382382').channel)

        # new contact on Airtel (some overlap) should send with the Tigo channel since it is newest
        msg = self.send_message(['+250738382382'], "Sent to a Airtel number")
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(tigo, msg.channel)

        # add a voice caller
        caller = Channel.add_call_channel(self.org, self.user, self.tel_channel)

        # set our affinity to the caller (ie, they were on an ivr call)
        ContactURN.objects.filter(path='+250788382382').update(channel=caller)
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=ContactURN.objects.get(path='+250788382382')))

        # change channel numbers to be shortcodes, i.e. no overlap with contact numbers
        mtn.address = '1234'
        mtn.save()
        tigo.address = '1235'
        tigo.save()

        self.org.clear_cached_channels()

        # should return the newest channel which is TIGO
        msg = self.send_message(['+250788382382'], "Sent to an MTN number, but with shortcode channels")
        self.assertEqual(tigo, msg.channel)
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))

        # if we have prefixes matching set should honor those
        mtn.config = {Channel.CONFIG_SHORTCODE_MATCHING_PREFIXES: ['25078', '25072']}
        mtn.save()

        self.org.clear_cached_channels()

        msg = self.send_message(['+250788382382'], "Sent to an MTN number with shortcode channels and prefixes set")
        self.assertEqual(mtn, msg.channel)
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))

        msg = self.send_message(['+250728382382'], "Sent to a TIGO number with shortcode channels and prefixes set")
        self.assertEqual(mtn, msg.channel)
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))

        # check for twitter
        self.assertEqual(self.twitter_channel, self.org.get_send_channel(scheme=TWITTER_SCHEME))

        contact = self.create_contact("Billy", number="+250722222222", twitter="billy_bob")
        twitter_urn = contact.get_urn(schemes=[TWITTER_SCHEME])
        self.assertEqual(self.twitter_channel, self.org.get_send_channel(contact_urn=twitter_urn))

    def test_message_splitting(self):
        # external API requires messages to be <= 160 chars
        self.tel_channel.channel_type = 'EX'
        self.tel_channel.save()

        msg = Msg.create_outgoing(self.org, self.user, 'tel:+250738382382', 'x' * 400)  # 400 chars long
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(3, Msg.objects.get(pk=msg.id).msg_count)

        # Nexmo limit is 1600
        self.tel_channel.channel_type = 'NX'
        self.tel_channel.save()
        cache.clear()  # clear the channel from cache

        msg = Msg.create_outgoing(self.org, self.user, 'tel:+250738382382', 'y' * 400)
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(self.tel_channel, Msg.objects.get(pk=msg.id).channel)
        self.assertEqual(1, Msg.objects.get(pk=msg.id).msg_count)

    def test_ensure_normalization(self):
        self.tel_channel.country = 'RW'
        self.tel_channel.save()

        contact1 = self.create_contact("contact1", "0788111222")
        contact2 = self.create_contact("contact2", "+250788333444")
        contact3 = self.create_contact("contact3", "+18006927753")

        self.org.normalize_contact_tels()

        norm_c1 = Contact.objects.get(pk=contact1.pk)
        norm_c2 = Contact.objects.get(pk=contact2.pk)
        norm_c3 = Contact.objects.get(pk=contact3.pk)

        self.assertEqual(norm_c1.get_urn(TEL_SCHEME).path, "+250788111222")
        self.assertEqual(norm_c2.get_urn(TEL_SCHEME).path, "+250788333444")
        self.assertEqual(norm_c3.get_urn(TEL_SCHEME).path, "+18006927753")

    def test_channel_create(self):

        # can't use an invalid scheme for a fixed-scheme channel type
        with self.assertRaises(ValueError):
            Channel.create(self.org, self.user, 'KE', 'AT', None, '+250788123123',
                           config=dict(username='at-user', api_key='africa-key'),
                           uuid='00000000-0000-0000-0000-000000001234',
                           schemes=['fb'])

        # a scheme is required
        with self.assertRaises(ValueError):
            Channel.create(self.org, self.user, 'US', 'EX', None, '+12065551212',
                           uuid='00000000-0000-0000-0000-000000001234',
                           schemes=[])

        # country channels can't have scheme
        with self.assertRaises(ValueError):
            Channel.create(self.org, self.user, 'US', 'EX', None, '+12065551212',
                           uuid='00000000-0000-0000-0000-000000001234',
                           schemes=['fb'])

    def test_delete(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        self.login(self.user)

        # a message, a call, and a broadcast
        msg = self.send_message(['250788382382'], "How is it going?")
        call = ChannelEvent.create(self.tel_channel, "tel:+250788383385", ChannelEvent.TYPE_CALL_IN, timezone.now(), {})

        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.tel_channel, msg.channel)
        self.assertEqual(1, Msg.get_messages(self.org).count())
        self.assertEqual(1, ChannelEvent.get_all(self.org).count())
        self.assertEqual(1, Broadcast.get_broadcasts(self.org).count())

        # start off in the pending state
        self.assertEqual('P', msg.status)

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]), self.user)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]),
                                        post_data=dict(remove=True), user=self.user)
        self.assertRedirect(response, reverse("orgs.org_home"))

        msg = Msg.objects.get(pk=msg.pk)
        self.assertIsNotNone(msg.channel)
        self.assertFalse(msg.channel.is_active)
        self.assertEqual(self.org, msg.org)

        # queued messages for the channel should get marked as failed
        self.assertEqual('F', msg.status)

        call = ChannelEvent.objects.get(pk=call.pk)
        self.assertIsNotNone(call.channel)
        self.assertFalse(call.channel.is_active)

        self.assertEqual(self.org, call.org)

        broadcast = Broadcast.objects.get(pk=msg.broadcast.pk)
        self.assertEqual(self.org, broadcast.org)

        # should still be considered that user's message, call and broadcast
        self.assertEqual(1, Msg.get_messages(self.org).count())
        self.assertEqual(1, ChannelEvent.get_all(self.org).count())
        self.assertEqual(1, Broadcast.get_broadcasts(self.org).count())

        # syncing this channel should result in a release
        post_data = dict(cmds=[dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # our response should contain a release
        self.assertHasCommand('rel', response)

        # create a channel
        channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                 secret=Channel.generate_secret(), gcm_id="123")

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]), self.superuser)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]),
                                        post_data=dict(remove=True), user=self.superuser)
        self.assertRedirect(response, reverse("orgs.org_home"))

        # create a channel
        channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                 secret=Channel.generate_secret(), gcm_id="123")

        # add channel trigger
        from temba.triggers.models import Trigger
        Trigger.objects.create(org=self.org, flow=self.create_flow(), channel=channel,
                               modified_by=self.admin, created_by=self.admin)

        self.assertTrue(Trigger.objects.filter(channel=channel, is_active=True))

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]),
                                        post_data=dict(remove=True), user=self.superuser)

        self.assertRedirect(response, reverse("orgs.org_home"))

        # channel trigger should have be removed
        self.assertFalse(Trigger.objects.filter(channel=channel, is_active=True))

    def test_list(self):
        # de-activate existing channels
        Channel.objects.all().update(is_active=False)

        # list page redirects to claim page
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertRedirect(response, reverse('channels.channel_claim'))

        # unless you're a superuser
        self.login(self.superuser)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['object_list']), [])

        # re-activate one of the channels so org has a single channel
        self.tel_channel.is_active = True
        self.tel_channel.save()

        # list page now redirects to channel read page
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertRedirect(response, reverse('channels.channel_read', args=[self.tel_channel.uuid]))

        # unless you're a superuser
        self.login(self.superuser)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['object_list']), [self.tel_channel])

        # re-activate other channel so org now has two channels
        self.twitter_channel.is_active = True
        self.twitter_channel.save()

        # no-more redirection for anyone
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['object_list']), {self.tel_channel, self.twitter_channel})

        # clear out the phone and name for the Android channel
        self.tel_channel.name = None
        self.tel_channel.address = None
        self.tel_channel.save()
        response = self.client.get(reverse('channels.channel_list'))
        self.assertContains(response, "Unknown")
        self.assertContains(response, "Android Phone")

    def test_channel_status(self):
        # visit page as a viewer
        self.login(self.user)
        response = self.client.get('/', follow=True)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")
        self.assertNotIn('delayed_syncevents', response.context, msg="Found delayed_syncevents in context")

        # visit page as superuser
        self.login(self.superuser)
        response = self.client.get('/', follow=True)
        # superusers doesn't have orgs thus cannot have both values
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")
        self.assertNotIn('delayed_syncevents', response.context, msg="Found delayed_syncevents in context")

        # visit page as administrator
        self.login(self.admin)
        response = self.client.get('/', follow=True)

        # there is not unsent nor delayed syncevents
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")
        self.assertNotIn('delayed_syncevents', response.context, msg="Found delayed_syncevents in context")

        # replace existing channels with a single Android device
        Channel.objects.update(is_active=False)
        channel = Channel.create(self.org, self.user, None, Channel.TYPE_ANDROID, None, "+250781112222",
                                 gcm_id="asdf", secret="asdf", created_on=(timezone.now() - timedelta(hours=2)))

        response = self.client.get('/', Follow=True)
        self.assertNotIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # simulate a sync in back in two hours
        post_data = dict(cmds=[
                         # device details status
                         dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60",
                              net="UMTS", pending=[], retry=[])])
        self.sync(channel, post_data)
        sync_event = SyncEvent.objects.all()[0]
        sync_event.created_on = timezone.now() - timedelta(hours=2)
        sync_event.save()

        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # add a message, just sent so shouldn't have delayed
        msg = Msg.create_outgoing(self.org, self.user, 'tel:250788123123', "test")
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # but put it in the past
        msg.delete()
        msg = Msg.create_outgoing(self.org, self.user, 'tel:250788123123', "test",
                                  created_on=timezone.now() - timedelta(hours=3))
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # if there is a successfully sent message after sms was created we do not consider it as delayed
        success_msg = Msg.create_outgoing(self.org, self.user, 'tel:+250788123123', "success-send",
                                          created_on=timezone.now() - timedelta(hours=2))
        success_msg.sent_on = timezone.now() - timedelta(hours=2)
        success_msg.status = 'S'
        success_msg.save()
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # test that editors have the channel of the the org the are using
        other_user = self.create_user("Other")
        self.create_secondary_org()
        self.org2.administrators.add(other_user)
        self.org.editors.add(other_user)
        self.assertFalse(self.org2.channels.all())

        self.login(other_user)

        other_user.set_org(self.org2)

        self.assertEqual(self.org2, other_user.get_org())
        response = self.client.get('/', follow=True)
        self.assertNotIn('channel_type', response.context, msg="Found channel_type in context")

        other_user.set_org(self.org)

        self.assertEqual(1, self.org.channels.filter(is_active=True).count())
        self.assertEqual(self.org, other_user.get_org())

        response = self.client.get('/', follow=True)
        # self.assertIn('channel_type', response.context)

    def sync(self, channel, post_data=None, signature=None):
        if not post_data:
            post_data = "{}"
        else:
            post_data = json.dumps(post_data)

        ts = int(time.time())
        if not signature:

            # sign the request
            key = str(channel.secret) + str(ts)
            signature = hmac.new(key=force_bytes(key), msg=force_bytes(post_data), digestmod=hashlib.sha256).digest()

            # base64 and url sanitize
            signature = quote(base64.urlsafe_b64encode(signature))

        return self.client.post("%s?signature=%s&ts=%d" % (reverse('sync', args=[channel.pk]), signature, ts),
                                content_type='application/json', data=post_data)

    def test_update(self):
        update_url = reverse('channels.channel_update', args=[self.tel_channel.id])

        # only user of the org can view the update page of a channel
        self.client.logout()
        self.login(self.user)
        response = self.client.get(update_url)
        self.assertEqual(302, response.status_code)

        self.login(self.user)
        # visit the channel's update page as a manager within the channel's organization
        self.org.administrators.add(self.user)
        response = self.fetch_protected(update_url, self.user)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], update_url)

        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel")
        self.assertEqual(channel.address, "+250785551212")

        postdata = dict()
        postdata['name'] = "Test Channel Update1"
        postdata['address'] = "+250785551313"

        self.login(self.user)
        response = self.client.post(update_url, postdata, follow=True)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update1")
        self.assertEqual(channel.address, "+250785551313")

        # if we change the channel to a twilio type, shouldn't be able to edit our address
        channel.channel_type = 'T'
        channel.save()

        response = self.client.get(update_url)
        self.assertNotIn('address', response.context['form'].fields)

        # bring it back to android
        channel.channel_type = Channel.TYPE_ANDROID
        channel.save()

        # visit the channel's update page as administrator
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        response = self.fetch_protected(update_url, self.user)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], update_url)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update1")
        self.assertEqual(channel.address, "+250785551313")

        postdata = dict()
        postdata['name'] = "Test Channel Update2"
        postdata['address'] = "+250785551414"

        response = self.fetch_protected(update_url, self.user, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update2")
        self.assertEqual(channel.address, "+250785551414")

        # visit the channel's update page as superuser
        self.superuser.set_org(self.org)
        response = self.fetch_protected(update_url, self.superuser)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], update_url)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update2")
        self.assertEqual(channel.address, "+250785551414")

        postdata = dict()
        postdata['name'] = "Test Channel Update3"
        postdata['address'] = "+250785551515"

        response = self.fetch_protected(update_url, self.superuser, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update3")
        self.assertEqual(channel.address, "+250785551515")

        # make sure channel works with alphanumeric numbers
        channel.address = "EATRIGHT"
        self.assertEqual("EATRIGHT", channel.get_address_display())
        self.assertEqual("EATRIGHT", channel.get_address_display(e164=True))

        # change channel type to Twitter
        channel.channel_type = 'TT'
        channel.schemes = [TWITTER_SCHEME]
        channel.address = 'billy_bob'
        channel.scheme = 'twitter'
        channel.config = {'handle_id': 12345, 'oauth_token': 'abcdef', 'oauth_token_secret': '23456'}
        channel.save()

        self.assertEqual('@billy_bob', channel.get_address_display())
        self.assertEqual('@billy_bob', channel.get_address_display(e164=True))

        response = self.fetch_protected(update_url, self.user)
        self.assertEqual(200, response.status_code)
        self.assertIn('name', response.context['fields'])
        self.assertIn('alert_email', response.context['fields'])
        self.assertIn('address', response.context['fields'])
        self.assertNotIn('country', response.context['fields'])

        postdata = dict()
        postdata['name'] = "Twitter2"
        postdata['alert_email'] = "bob@example.com"
        postdata['address'] = "billy_bob"

        self.fetch_protected(update_url, self.user, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Twitter2")
        self.assertEqual(channel.alert_email, "bob@example.com")
        self.assertEqual(channel.address, "billy_bob")

    def test_read(self):
        post_data = dict(cmds=[
                         # device details status
                         dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60",
                              net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        self.sync(self.tel_channel, post_data)
        post_data = dict(cmds=[
                         # device details status
                         dict(cmd="status", p_sts="FUL", p_src="AC", p_lvl="100",
                              net="WIFI", pending=[], retry=[])])

        # now send the channel's updates
        self.sync(self.tel_channel, post_data)
        self.assertEqual(2, SyncEvent.objects.all().count())

        # non-org users can't view our channels
        self.login(self.non_org_user)
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertRedirect(response, reverse('orgs.org_choose'))

        # org users can
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.user)

        self.assertEqual(len(response.context['source_stats']), len(SyncEvent.objects.values_list('power_source', flat=True).distinct()))
        self.assertEqual('AC', response.context['source_stats'][0][0])
        self.assertEqual(1, response.context['source_stats'][0][1])
        self.assertEqual('BAT', response.context['source_stats'][1][0])
        self.assertEqual(1, response.context['source_stats'][0][1])

        self.assertEqual(len(response.context['network_stats']), len(SyncEvent.objects.values_list('network_type', flat=True).distinct()))
        self.assertEqual('UMTS', response.context['network_stats'][0][0])
        self.assertEqual(1, response.context['network_stats'][0][1])
        self.assertEqual('WIFI', response.context['network_stats'][1][0])
        self.assertEqual(1, response.context['network_stats'][1][1])

        self.assertTrue(len(response.context['latest_sync_events']) <= 5)

        response = self.fetch_protected(reverse('orgs.org_home'), self.admin)
        self.assertNotContains(response, 'Enable Voice')

        # Add twilio credentials to make sure we can add calling for our android channel
        twilio_config = {ACCOUNT_SID: 'SID', ACCOUNT_TOKEN: 'TOKEN', APPLICATION_SID: 'APP SID'}
        config = self.org.config
        config.update(twilio_config)
        self.org.config = config
        self.org.save(update_fields=['config'])

        response = self.fetch_protected(reverse('orgs.org_home'), self.admin)
        self.assertTrue(self.org.is_connected_to_twilio())
        self.assertContains(response, 'Enable Voice')

        two_hours_ago = timezone.now() - timedelta(hours=2)

        # make sure our channel is old enough to trigger alerts
        self.tel_channel.created_on = two_hours_ago
        self.tel_channel.save()

        # delayed sync status
        for sync in SyncEvent.objects.all():
            sync.created_on = two_hours_ago
            sync.save()

        # add a message, just sent so shouldn't be delayed
        Msg.create_outgoing(self.org, self.user, 'tel:250785551212', 'delayed message', created_on=two_hours_ago)

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.admin)
        self.assertIn('delayed_sync_event', response.context_data.keys())
        self.assertIn('unsent_msgs_count', response.context_data.keys())

        # with superuser
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)

        # now that we can access the channel, which messages do we display in the chart?
        joe = self.create_contact('Joe', '+2501234567890')
        test_contact = Contact.get_test_contact(self.admin)

        # should have two series, one for incoming one for outgoing
        self.assertEqual(2, len(response.context['message_stats']))

        # but only an outgoing message so far
        self.assertEqual(0, len(response.context['message_stats'][0]['data']))
        self.assertEqual(1, response.context['message_stats'][1]['data'][-1]['count'])

        # we have one row for the message stats table
        self.assertEqual(1, len(response.context['message_stats_table']))
        # only one outgoing message
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # send messages with a test contact
        Msg.create_incoming(self.tel_channel, six.text_type(test_contact.get_urn()), 'This incoming message will not be counted')
        Msg.create_outgoing(self.org, self.user, test_contact, 'This outgoing message will not be counted')

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)

        # nothing should change since it's a test contact
        self.assertEqual(0, len(response.context['message_stats'][0]['data']))
        self.assertEqual(1, response.context['message_stats'][1]['data'][-1]['count'])

        # no change on the table starts too
        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # send messages with a normal contact
        Msg.create_incoming(self.tel_channel, six.text_type(joe.get_urn(TEL_SCHEME)), 'This incoming message will be counted')
        Msg.create_outgoing(self.org, self.user, joe, 'This outgoing message will be counted')

        # now we have an inbound message and two outbounds
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.context['message_stats'][0]['data'][-1]['count'])

        # this assertion is problematic causing time-sensitive failures, to reconsider
        # self.assertEqual(2, response.context['message_stats'][1]['data'][-1]['count'])

        # message stats table have an inbound and two outbounds in the last month
        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # test cases for IVR messaging, make our relayer accept calls
        self.tel_channel.role = 'SCAR'
        self.tel_channel.save()

        from temba.msgs.models import IVR
        Msg.create_incoming(self.tel_channel, six.text_type(test_contact.get_urn()), 'incoming ivr as a test contact', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, test_contact, 'outgoing ivr as a test contact', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)

        # nothing should have changed
        self.assertEqual(2, len(response.context['message_stats']))

        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # now let's create an ivr interaction from a real contact
        Msg.create_incoming(self.tel_channel, six.text_type(joe.get_urn()), 'incoming ivr', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, joe, 'outgoing ivr', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)

        self.assertEqual(4, len(response.context['message_stats']))
        self.assertEqual(1, response.context['message_stats'][2]['data'][0]['count'])
        self.assertEqual(1, response.context['message_stats'][3]['data'][0]['count'])

        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['outgoing_ivr_count'])

    def test_invalid(self):

        # Must be POST
        response = self.client.get("%s?signature=sig&ts=123" % (reverse('sync', args=[100])), content_type='application/json')
        self.assertEqual(500, response.status_code)

        # Unknown channel
        response = self.client.post("%s?signature=sig&ts=123" % (reverse('sync', args=[999])), content_type='application/json')
        self.assertEqual(200, response.status_code)
        self.assertEqual('rel', response.json()['cmds'][0]['cmd'])

        # too old
        ts = int(time.time()) - 60 * 16
        response = self.client.post("%s?signature=sig&ts=%d" % (reverse('sync', args=[self.tel_channel.pk]), ts), content_type='application/json')
        self.assertEqual(401, response.status_code)
        self.assertEqual(3, response.json()['error_id'])

    def test_claim(self):
        # no access for regular users
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertLoginRedirect(response)

        # editor can access
        self.login(self.editor)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEqual(200, response.status_code)

        # as can admins
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context['twilio_countries'], "Belgium, Canada, Finland, Norway, Poland, Spain, "
                                                               "Sweden, United Kingdom or United States")

        # one recommended channel (Mtarget in Rwanda)
        self.assertEqual(len(response.context['recommended_channels']), 1)

        self.assertEqual(response.context['channel_types']['PHONE'][0].code, 'T')
        self.assertEqual(response.context['channel_types']['PHONE'][1].code, 'TMS')
        self.assertEqual(response.context['channel_types']['PHONE'][2].code, 'NX')
        self.assertEqual(response.context['channel_types']['PHONE'][3].code, 'CT')
        self.assertEqual(response.context['channel_types']['PHONE'][4].code, 'EX')

        self.org.timezone = 'Canada/Central'
        self.org.save()

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEqual(200, response.status_code)

        self.assertEqual(len(response.context['recommended_channels']), 3)
        self.assertEqual(response.context['recommended_channels'][0].code, 'T')
        self.assertEqual(response.context['recommended_channels'][1].code, 'TMS')
        self.assertEqual(response.context['recommended_channels'][2].code, 'NX')

        self.assertEqual(response.context['channel_types']['PHONE'][0].code, 'CT')
        self.assertEqual(response.context['channel_types']['PHONE'][1].code, 'EX')
        self.assertEqual(response.context['channel_types']['PHONE'][2].code, 'IB')
        self.assertEqual(response.context['channel_types']['PHONE'][3].code, 'JS')

    def test_register_and_claim_android(self):
        # remove our explicit country so it needs to be derived from channels
        self.org.country = None
        self.org.save()

        Channel.objects.all().delete()

        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])

        # must be a post
        response = self.client.get(reverse('register'), content_type='application/json')
        self.assertEqual(500, response.status_code)

        # try a legit register
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(200, response.status_code)

        android1 = Channel.objects.get()
        self.assertIsNone(android1.org)
        self.assertIsNone(android1.address)
        self.assertIsNone(android1.alert_email)
        self.assertEqual(android1.country, 'RW')
        self.assertEqual(android1.device, 'Nexus')
        self.assertEqual(android1.gcm_id, 'GCM111')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertTrue(android1.secret)
        self.assertTrue(android1.claim_code)
        self.assertEqual(android1.created_by, get_anonymous_user())

        # check channel JSON in response
        response_json = response.json()
        self.assertEqual(response_json, dict(cmds=[dict(cmd='reg',
                                                        relayer_claim_code=android1.claim_code,
                                                        relayer_secret=android1.secret,
                                                        relayer_id=android1.id)]))

        # try registering again with same details
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        android1 = Channel.objects.get()
        response_json = response.json()

        self.assertEqual(response_json, dict(cmds=[dict(cmd='reg',
                                                        relayer_claim_code=android1.claim_code,
                                                        relayer_secret=android1.secret,
                                                        relayer_id=android1.id)]))

        # try to claim as non-admin
        self.login(self.user)
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="0788123123"))
        self.assertLoginRedirect(response)

        # try to claim with an invalid phone number
        self.login(self.admin)
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="078123"))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'phone_number', "Invalid phone number, try again.")

        # claim our channel
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="0788123123"))

        # redirect to welcome page
        self.assertIn('success', response.get('Location', None))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # channel is updated with org details and claim code is now blank
        android1.refresh_from_db()
        secret = android1.secret
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123123')  # normalized
        self.assertEqual(android1.alert_email, self.admin.email)  # the logged-in user
        self.assertEqual(android1.gcm_id, 'GCM111')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertFalse(android1.claim_code)

        # try having a device register again
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        # should return same channel but with a new claim code and secret
        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123123')
        self.assertEqual(android1.alert_email, self.admin.email)
        self.assertEqual(android1.gcm_id, 'GCM111')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertEqual(android1.is_active, True)
        self.assertTrue(android1.claim_code)
        self.assertNotEqual(android1.secret, secret)

        # should be able to claim again
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="0788123123"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # try having a device register yet again with new GCM ID
        reg_data['cmds'][0]['gcm_id'] = "GCM222"
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        # should return same channel but with GCM updated
        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123123')
        self.assertEqual(android1.alert_email, self.admin.email)
        self.assertEqual(android1.gcm_id, 'GCM222')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertEqual(android1.is_active, True)

        # we can claim again with new phone number
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="+250788123124"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123124')
        self.assertEqual(android1.alert_email, self.admin.email)
        self.assertEqual(android1.gcm_id, 'GCM222')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertEqual(android1.is_active, True)

        # release and then register with same details and claim again
        old_uuid = android1.uuid
        android1.release()

        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        claim_code = response.json()['cmds'][0]['relayer_claim_code']
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=claim_code, phone_number="+250788123124"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        android1.refresh_from_db()

        self.assertNotEqual(android1.uuid, old_uuid)  # inactive channel now has new UUID

        # and we have a new Android channel with our UUID
        android2 = Channel.objects.get(is_active=True)
        self.assertNotEqual(android2, android1)
        self.assertEqual(android2.uuid, 'uuid')

        # try to claim a bogus channel
        response = self.client.post(reverse('channels.channel_claim_android'), dict(claim_code="Your Mom"))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'claim_code', "Invalid claim code, please check and try again.")

        # check our primary tel channel is the same as our outgoing
        default_sender = self.org.get_send_channel(TEL_SCHEME)
        self.assertEqual(default_sender, android2)
        self.assertEqual(default_sender, self.org.get_receive_channel(TEL_SCHEME))
        self.assertFalse(default_sender.is_delegate_sender())

        response = self.client.get(reverse('channels.channel_bulk_sender_options'))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('channels.channel_create_bulk_sender') + "?connection=NX",
                                    dict(connection='NX'))
        self.assertFormError(response, 'form', 'channel', "Can't add sender for that number")

        # try to claim a bulk Nexmo sender (without adding Nexmo account to org)
        claim_nexmo_url = reverse('channels.channel_create_bulk_sender') + "?connection=NX&channel=%d" % android2.pk
        response = self.client.post(claim_nexmo_url, dict(connection='NX', channel=android2.pk))
        self.assertFormError(response, 'form', 'connection', "A connection to a Nexmo account is required")

        # send channel is still our Android device
        self.assertEqual(self.org.get_send_channel(TEL_SCHEME), android2)
        self.assertFalse(self.org.is_connected_to_nexmo())

        # now connect to nexmo
        with patch('temba.utils.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
            with patch('nexmo.Client.create_application') as create_app:
                create_app.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
                self.org.connect_nexmo('123', '456', self.admin)
                self.org.save()
        self.assertTrue(self.org.is_connected_to_nexmo())

        # now adding Nexmo bulk sender should work
        response = self.client.post(claim_nexmo_url, dict(connection='NX', channel=android2.pk))
        self.assertRedirect(response, reverse('orgs.org_home'))

        # new Nexmo channel created for delegated sending
        nexmo = self.org.get_send_channel(TEL_SCHEME)
        self.assertEqual(nexmo.channel_type, 'NX')
        self.assertEqual(nexmo.parent, android2)
        self.assertTrue(nexmo.is_delegate_sender())
        self.assertEqual(nexmo.tps, 1)
        channel_config = nexmo.config
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_API_KEY], '123')
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_API_SECRET], '456')
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_APP_ID], 'app-id')
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_APP_PRIVATE_KEY], 'private-key')

        # reading our nexmo channel should now offer a disconnect option
        nexmo = self.org.channels.filter(channel_type='NX').first()
        response = self.client.get(reverse('channels.channel_read', args=[nexmo.uuid]))
        self.assertContains(response, 'Disable Bulk Sending')

        # receiving still job of our Android device
        self.assertEqual(self.org.get_receive_channel(TEL_SCHEME), android2)

        # re-register device with country as US
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM222", uuid='uuid'),
                              dict(cmd='status', cc='US', dev="Nexus 5X")])
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        # channel country and device updated
        android2.refresh_from_db()
        self.assertEqual(android2.country, 'US')
        self.assertEqual(android2.device, "Nexus 5X")
        self.assertEqual(android2.org, self.org)
        self.assertEqual(android2.gcm_id, "GCM222")
        self.assertEqual(android2.uuid, "uuid")
        self.assertTrue(android2.is_active)

        # set back to RW...
        android2.country = 'RW'
        android2.save()

        # our country is RW
        self.assertEqual(self.org.get_country_code(), 'RW')

        # remove nexmo
        nexmo.release()

        self.assertEqual(self.org.get_country_code(), 'RW')

        # register another device with country as US
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM444", uuid='uuid4'),
                              dict(cmd='status', cc='US', dev="Nexus 6P")])
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')

        claim_code = response.json()['cmds'][0]['relayer_claim_code']

        # try to claim it...
        self.client.post(reverse('channels.channel_claim_android'), dict(claim_code=claim_code, phone_number="12065551212"))

        # should work, can have two channels in different countries
        channel = Channel.objects.get(country='US')
        self.assertEqual(channel.address, '+12065551212')

        self.assertEqual(Channel.objects.filter(org=self.org, is_active=True).count(), 2)

        # normalize a URN with a fully qualified number
        number, valid = URN.normalize_number('+12061112222', None)
        self.assertTrue(valid)

        # not international format
        number, valid = URN.normalize_number('0788383383', None)
        self.assertFalse(valid)

        # get our send channel without a URN, should just default to last
        default_channel = self.org.get_send_channel(TEL_SCHEME)
        self.assertEqual(default_channel, channel)

        # get our send channel for a Rwandan URN
        rwanda_channel = self.org.get_send_channel(TEL_SCHEME, ContactURN.create(self.org, None, 'tel:+250788383383'))
        self.assertEqual(rwanda_channel, android2)

        # and a US one
        us_channel = self.org.get_send_channel(TEL_SCHEME, ContactURN.create(self.org, None, 'tel:+12065555353'))
        self.assertEqual(us_channel, channel)

        # a different country altogether should just give us the default
        us_channel = self.org.get_send_channel(TEL_SCHEME, ContactURN.create(self.org, None, 'tel:+593997290044'))
        self.assertEqual(us_channel, channel)

        self.org = Org.objects.get(id=self.org.id)
        self.assertIsNone(self.org.get_country_code())

        # yet another registration in rwanda
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM555", uuid='uuid5'),
                              dict(cmd='status', cc='RW', dev="Nexus 5")])
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        claim_code = response.json()['cmds'][0]['relayer_claim_code']

        # try to claim it with number taken by other Android channel
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=claim_code, phone_number="+250788123124"))
        self.assertFormError(response, 'form', 'phone_number', "Another channel has this number. Please remove that channel first.")

        # create channel in another org
        self.create_secondary_org()
        Channel.create(self.org2, self.admin2, 'RW', 'A', "", "+250788382382")

        # can claim it with this number, and because it's a fully qualified RW number, doesn't matter that channel is US
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=claim_code, phone_number="+250788382382"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # should be added with RW as the country
        self.assertTrue(Channel.objects.get(address='+250788382382', country='RW', org=self.org))

    def test_search_nexmo(self):
        self.login(self.admin)
        self.org.channels.update(is_active=False)
        self.channel = Channel.create(self.org, self.user, 'RW', 'NX', None, '+250788123123',
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.nexmo_uuid = str(uuid.uuid4())
        nexmo_config = {NEXMO_KEY: '1234', NEXMO_SECRET: '1234', NEXMO_UUID: self.nexmo_uuid,
                        NEXMO_APP_ID: 'nexmo-app-id', NEXMO_APP_PRIVATE_KEY: 'nexmo-private-key'}

        org = self.channel.org

        config = org.config
        config.update(nexmo_config)
        org.config = config
        org.save()

        search_nexmo_url = reverse('channels.channel_search_nexmo')

        response = self.client.get(search_nexmo_url)
        self.assertIn('area_code', response.context['form'].fields)
        self.assertIn('country', response.context['form'].fields)

        with patch('requests.get') as nexmo_get:
            nexmo_get.side_effect = [MockResponse(200,
                                                  '{"count":1,"numbers":[{"features": ["SMS", "VOICE"], '
                                                  '"type":"mobile-lvn","country":"US","msisdn":"13607884540"}] }'),
                                     MockResponse(200,
                                                  '{"count":1,"numbers":[{"features": ["SMS", "VOICE"], '
                                                  '"type":"mobile-lvn","country":"US","msisdn":"13607884550"}] }'),
                                     ]

            post_data = dict(country='US', area_code='360')
            response = self.client.post(search_nexmo_url, post_data, follow=True)

            self.assertEqual(response.json(), ['+1 360-788-4540', '+1 360-788-4550'])

    def test_plivo_search_numbers(self):
        self.login(self.admin)

        plivo_search_url = reverse('channels.channel_search_plivo')

        with patch('requests.get') as plivo_get:
            plivo_get.return_value = MockResponse(200, json.dumps(dict(objects=[])))

            response = self.client.post(plivo_search_url, dict(country='US', area_code=''), follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'error')

            # missing key to throw exception
            plivo_get.return_value = MockResponse(200, json.dumps(dict()))
            response = self.client.post(plivo_search_url, dict(country='US', area_code=''), follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'error')

            plivo_get.side_effect = [
                MockResponse(200, json.dumps(dict())),  # get account in pre_process
                MockResponse(400, "Bad request")  # failed search numbers
            ]
            response = self.client.post(plivo_search_url, dict(country='US', area_code=''), follow=True)

            self.assertContains(response, 'Bad request')

    def test_release(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        # register and claim an Android channel
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])
        self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        android = Channel.objects.get()
        self.client.post(reverse('channels.channel_claim_android'),
                         dict(claim_code=android.claim_code, phone_number="0788123123"))
        android.refresh_from_db()

        # connect org to Nexmo and add bulk sender
        with patch('temba.utils.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
            with patch('nexmo.Client.create_application') as create_app:
                create_app.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
                self.org.connect_nexmo('123', '456', self.admin)
                self.org.save()

        claim_nexmo_url = reverse('channels.channel_create_bulk_sender') + "?connection=NX&channel=%d" % android.pk
        self.client.post(claim_nexmo_url, dict(connection='NX', channel=android.pk))
        nexmo = Channel.objects.get(channel_type='NX')

        android.release()

        # check that some details are cleared and channel is now in active
        self.assertFalse(android.is_active)

        # Nexmo delegate should have been released as well
        nexmo.refresh_from_db()
        self.assertFalse(nexmo.is_active)

        Channel.objects.all().delete()

        # register and claim an Android channel
        reg_data = dict(cmds=[dict(cmd="fcm", fcm_id="FCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])
        self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        android = Channel.objects.get()
        self.client.post(reverse('channels.channel_claim_android'),
                         dict(claim_code=android.claim_code, phone_number="0788123123"))
        android.refresh_from_db()

        android.release()

        # check that some details are cleared and channel is now in active
        self.assertFalse(android.is_active)

    @override_settings(IS_PROD=True)
    def test_release_ivr_channel(self):

        # create outgoing call for the channel
        contact = self.create_contact('Bruno Mars', '+252788123123')
        call = IVRCall.create_outgoing(self.tel_channel, contact, contact.get_urn(TEL_SCHEME), self.admin)

        self.assertNotEqual(call.status, ChannelSession.INTERRUPTED)
        self.tel_channel.release()

        call.refresh_from_db()
        self.assertEqual(call.status, ChannelSession.INTERRUPTED)

    def test_unclaimed(self):
        response = self.sync(self.unclaimed_channel)
        self.assertEqual(200, response.status_code)
        response = response.json()

        # should be a registration command containing a new claim code
        self.assertEqual(response['cmds'][0]['cmd'], 'reg')

        post_data = dict(cmds=[dict(cmd="status",
                                    org_id=self.unclaimed_channel.pk,
                                    p_lvl=84,
                                    net="WIFI",
                                    p_sts="CHA",
                                    p_src="USB",
                                    pending=[],
                                    retry=[])])

        # try syncing against the unclaimed channel that has a secret
        self.unclaimed_channel.secret = "999"
        self.unclaimed_channel.save()

        response = self.sync(self.unclaimed_channel, post_data=post_data)
        response = response.json()

        # registration command
        self.assertEqual(response['cmds'][0]['cmd'], 'reg')

        # claim the channel on the site
        self.unclaimed_channel.org = self.org
        self.unclaimed_channel.save()

        post_data = dict(cmds=[dict(cmd="status",
                                    org_id="-1",
                                    p_lvl=84,
                                    net="WIFI",
                                    p_sts="STATUS_CHARGING",
                                    p_src="USB",
                                    pending=[],
                                    retry=[])])

        response = self.sync(self.unclaimed_channel, post_data=post_data)
        response = response.json()

        # should now be a claim command in return
        self.assertEqual(response['cmds'][0]['cmd'], 'claim')

        # now try releasing the channel from the client
        post_data = dict(cmds=[dict(cmd="reset", p_id=1)])

        response = self.sync(self.unclaimed_channel, post_data=post_data)
        response = response.json()

        # channel should be released now
        channel = Channel.objects.get(pk=self.unclaimed_channel.pk)
        self.assertFalse(channel.is_active)

    def test_quota_exceeded(self):
        # set our org to be on the trial plan
        self.org.plan = FREE_PLAN
        self.org.save()
        self.org.topups.all().update(credits=10)

        self.assertEqual(10, self.org.get_credits_remaining())
        self.assertEqual(0, self.org.get_credits_used())

        # if we sync should get one message back
        self.send_message(['250788382382'], "How is it going?")

        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)
        response = response.json()
        self.assertEqual(1, len(response['cmds']))

        self.assertEqual(9, self.org.get_credits_remaining())
        self.assertEqual(1, self.org.get_credits_used())

        # let's create 10 other messages, this will put our last message above our quota
        for i in range(10):
            self.send_message(['250788382%03d' % i], "This is message # %d" % i)

        # should get the 10 messages we are allotted back, not the 11 that exist
        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)
        response = response.json()
        self.assertEqual(10, len(response['cmds']))

    def test_sync_broadcast_multiple_channels(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        channel2 = Channel.create(self.org, self.user, 'RW', 'A', name="Test Channel 2", address="+250785551313",
                                  role="SR", secret="12367", gcm_id="456")

        contact1 = self.create_contact("John Doe", '250788382382')
        contact2 = self.create_contact("John Doe", '250788383383')

        contact1_urn = contact1.get_urn()
        contact1_urn.channel = self.tel_channel
        contact1_urn.save()

        contact2_urn = contact2.get_urn()
        contact2_urn.channel = channel2
        contact2_urn.save()

        # send a broadcast to urn that have different preferred channels
        self.send_message(['250788382382', '250788383383'], "How is it going?")

        # Should contain messages for the the channel only
        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)

        self.tel_channel.refresh_from_db()

        response = response.json()
        cmds = response['cmds']
        self.assertEqual(1, len(cmds))
        self.assertEqual(len(cmds[0]['to']), 1)
        self.assertEqual(cmds[0]['to'][0]['phone'], '+250788382382')

        # Should contain messages for the the channel only
        response = self.sync(channel2)
        self.assertEqual(200, response.status_code)

        channel2.refresh_from_db()

        response = response.json()
        cmds = response['cmds']
        self.assertEqual(1, len(cmds))
        self.assertEqual(len(cmds[0]['to']), 1)
        self.assertEqual(cmds[0]['to'][0]['phone'], '+250788383383')

    def test_sync(self):
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        # create a payload from the client
        bcast = self.send_message(['250788382382', '250788383383'], "How is it going?")
        msg1 = bcast[0]
        msg2 = bcast[1]
        msg3 = self.send_message(['250788382382'], "What is your name?")
        msg4 = self.send_message(['250788382382'], "Do you have any children?")
        msg5 = self.send_message(['250788382382'], "What's my dog's name?")

        # an incoming message that should not be included even if it is still pending
        incoming_message = Msg.create_incoming(self.tel_channel, "tel:+250788382382", 'hey')
        incoming_message.status = PENDING
        incoming_message.save()

        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        # Check our sync point has all three messages queued for delivery
        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)

        # check last seen and gcm id were updated
        self.tel_channel.refresh_from_db()

        response = response.json()
        cmds = response['cmds']
        self.assertEqual(4, len(cmds))

        # assert that our first command is the two message broadcast
        cmd = cmds[0]
        self.assertEqual("How is it going?", cmd['msg'])
        self.assertIn('+250788382382', [m['phone'] for m in cmd['to']])
        self.assertIn('+250788383383', [m['phone'] for m in cmd['to']])

        self.assertTrue(msg1.pk in [m['id'] for m in cmd['to']])
        self.assertTrue(msg2.pk in [m['id'] for m in cmd['to']])

        # add another message we'll pretend is in retry to see that we exclude them from sync
        msg6 = self.send_message(['250788382382'], "Pretend this message is in retry on the client, don't send it on sync")

        # a pending outgoing message should be included
        Msg.create_outgoing(self.org, self.admin, msg6.contact, "Hello, we heard from you.")

        six_mins_ago = timezone.now() - timedelta(minutes=6)
        self.tel_channel.last_seen = six_mins_ago
        self.tel_channel.gcm_id = 'old_gcm_id'
        self.tel_channel.save(update_fields=['last_seen', 'gcm_id'])

        post_data = dict(cmds=[

            # device gcm data
            dict(cmd='gcm', gcm_id='12345', uuid='abcde'),

            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="60",
                 net="UMTS", org_id=8, retry=[msg6.pk], pending=[]),

            # pending incoming message that should be acknowledged but not updated
            dict(cmd="mt_sent", msg_id=incoming_message.pk, ts=date),

            # results for the outgoing messages
            dict(cmd="mt_sent", msg_id=msg1.pk, ts=date),
            dict(cmd="mt_sent", msg_id=msg2.pk, ts=date),
            dict(cmd="mt_dlvd", msg_id=msg3.pk, ts=date),
            dict(cmd="mt_error", msg_id=msg4.pk, ts=date),
            dict(cmd="mt_fail", msg_id=msg5.pk, ts=date),

            # a missed call
            dict(cmd="call", phone="2505551212", type='miss', ts=date),

            # incoming
            dict(cmd="call", phone="2505551212", type='mt', dur=10, ts=date),

            # incoming, invalid URN
            dict(cmd="call", phone="*", type='mt', dur=10, ts=date),

            # outgoing
            dict(cmd="call", phone="+250788383383", type='mo', dur=5, ts=date),

            # a new incoming message
            dict(cmd="mo_sms", phone="+250788383383", msg="This is giving me trouble", p_id="1", ts=date),

            # an incoming message from an empty contact
            dict(cmd="mo_sms", phone="", msg="This is spam", p_id="2", ts=date)])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        self.tel_channel.refresh_from_db()
        self.assertEqual(self.tel_channel.gcm_id, '12345')
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)

        # new batch, our ack and our claim command for new org
        self.assertEqual(4, len(response.json()['cmds']))
        self.assertContains(response, "Hello, we heard from you.")
        self.assertContains(response, "mt_bcast")

        # check that our messages were updated accordingly
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status='S', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='D', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='E', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='F', direction='O').count())

        # we should now have two incoming messages
        self.assertEqual(3, Msg.objects.filter(direction='I').count())

        # one of them should have an empty 'tel'
        self.assertTrue(Msg.objects.filter(direction='I', contact_urn__path='empty'))

        # We should now have one sync
        self.assertEqual(1, SyncEvent.objects.filter(channel=self.tel_channel).count())

        # check our channel gcm and uuid were updated
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)
        self.assertEqual('12345', self.tel_channel.gcm_id)
        self.assertEqual('abcde', self.tel_channel.uuid)

        # should ignore incoming messages without text
        post_data = dict(cmds=[
            # incoming msg without text
            dict(cmd="mo_sms", phone="+250788383383", p_id="1", ts=date),

        ])

        msgs_count = Msg.objects.all().count()
        response = self.sync(self.tel_channel, post_data)

        # no new message
        self.assertEqual(Msg.objects.all().count(), msgs_count)

        # set an email on our channel
        self.tel_channel.alert_email = 'fred@worldrelif.org'
        self.tel_channel.save()

        # We should not have an alert this time
        self.assertEqual(0, Alert.objects.all().count())

        # the case the status must be be reported
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="20", net="UMTS", retry=[], pending=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now have an Alert
        self.assertEqual(1, Alert.objects.all().count())

        # and at this time it must be not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # the case the status must be be reported but already notification sent
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should not create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # still not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # only one alert
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # clear the alerts
        Alert.objects.all().delete()

        # the case the status is in unknown state

        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="UNK", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # still only one alert
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # clear all the alerts
        Alert.objects.all().delete()

        # the case the status is in not charging state
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="NOT", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # first we have a new alert created
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        post_data = dict(cmds=[
            # device fcm data
            dict(cmd='fcm', fcm_id='12345', uuid='abcde')])

        response = self.sync(self.tel_channel, post_data)

        self.tel_channel.refresh_from_db()
        self.assertIsNone(self.tel_channel.gcm_id)
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)
        self.assertEqual(self.tel_channel.config[Channel.CONFIG_FCM_ID], '12345')

    def test_signing(self):
        # good signature
        self.assertEqual(200, self.sync(self.tel_channel).status_code)

        # bad signature, should result in 401 Unauthorized
        self.assertEqual(401, self.sync(self.tel_channel, signature="badsig").status_code)

    def test_ignore_android_incoming_msg_invalid_phone(self):
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        post_data = dict(cmds=[
            dict(cmd="mo_sms", phone="_@", msg="First message", p_id="1", ts=date)])

        response = self.sync(self.tel_channel, post_data)
        self.assertEqual(200, response.status_code)

        responses = response.json()
        cmds = responses['cmds']

        # check the server gave us responses for our message
        r0 = self.get_response(cmds, '1')

        self.assertIsNotNone(r0)
        self.assertEqual(r0['cmd'], 'ack')

    def test_inbox_duplication(self):

        # if the connection gets interrupted but some messages succeed, we want to make sure subsequent
        # syncs do not result in duplication of messages from the inbox
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        post_data = dict(cmds=[
            dict(cmd="mo_sms", phone="2505551212", msg="First message", p_id="1", ts=date),
            dict(cmd="mo_sms", phone="2505551212", msg="First message", p_id="2", ts=date),
            dict(cmd="mo_sms", phone="2505551212", msg="A second message", p_id="3", ts=date)
        ])

        response = self.sync(self.tel_channel, post_data)
        self.assertEqual(200, response.status_code)

        responses = response.json()
        cmds = responses['cmds']

        # check the server gave us responses for our messages
        r0 = self.get_response(cmds, '1')
        r1 = self.get_response(cmds, '2')
        r2 = self.get_response(cmds, '3')

        self.assertIsNotNone(r0)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

        # first two should have the same server id
        self.assertEqual(r0['extra'], r1['extra'])

        # One was a duplicate, should only have 2
        self.assertEqual(2, Msg.objects.filter(direction='I').count())

    def get_response(self, responses, p_id):
        for response in responses:
            if 'p_id' in response and response['p_id'] == p_id:
                return response

    @patch('nexmo.Client.update_call')
    @patch('nexmo.Client.create_application')
    def test_get_ivr_client(self, mock_create_application, mock_update_call):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_update_call.return_value = dict(uuid='12345')

        channel = Channel.create(self.org, self.user, 'RW', 'A', "Tigo", "+250725551212", secret="11111", gcm_id="456")
        self.assertIsNone(channel.get_ivr_client())

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        channel.channel_type = 'NX'
        channel.save()

        self.assertIsNotNone(channel.get_ivr_client())

        channel.release()
        self.assertIsNone(channel.get_ivr_client())

    def test_channel_status_processor(self):

        request = RequestFactory().get('/')
        request.user = self.admin

        def get_context(channel_type, role):
            Channel.objects.all().delete()
            Channel.create(
                self.org, self.admin, 'RW', channel_type, None, '1234',
                config=dict(username='junebug-user', password='junebug-pass', send_url='http://example.org/'),
                uuid='00000000-0000-0000-0000-000000001234', role=role)
            return channel_status_processor(request)

        Channel.objects.all().delete()
        no_channel_context = channel_status_processor(request)
        self.assertFalse(no_channel_context['has_outgoing_channel'])
        self.assertEqual(no_channel_context['is_ussd_channel'], False)

        sms_context = get_context('JN', Channel.ROLE_SEND)
        self.assertTrue(sms_context['has_outgoing_channel'])
        self.assertEqual(sms_context['is_ussd_channel'], False)

        ussd_context = get_context('JNU', Channel.ROLE_USSD)
        self.assertTrue(ussd_context['has_outgoing_channel'])
        self.assertEqual(ussd_context['is_ussd_channel'], True)


class ChannelBatchTest(TembaTest):

    def test_time_utils(self):
        now = timezone.now()
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)

        epoch = datetime_to_ms(now)
        self.assertEqual(ms_to_datetime(epoch), now)


class ChannelEventTest(TembaTest):

    def test_create(self):
        now = timezone.now()
        event = ChannelEvent.create(self.channel, "tel:+250783535665", ChannelEvent.TYPE_CALL_OUT, now, extra=dict(duration=300))

        contact = Contact.objects.get()
        self.assertEqual(six.text_type(contact.get_urn()), "tel:+250783535665")

        self.assertEqual(event.org, self.org)
        self.assertEqual(event.channel, self.channel)
        self.assertEqual(event.contact, contact)
        self.assertEqual(event.event_type, ChannelEvent.TYPE_CALL_OUT)
        self.assertEqual(event.occurred_on, now)
        self.assertEqual(event.extra['duration'], 300)


class ChannelEventCRUDLTest(TembaTest):

    def test_calls(self):
        now = timezone.now()
        ChannelEvent.create(self.channel, "tel:12345", ChannelEvent.TYPE_CALL_IN, now, dict(duration=600))
        ChannelEvent.create(self.channel, "tel:890", ChannelEvent.TYPE_CALL_IN_MISSED, now)
        ChannelEvent.create(self.channel, "tel:456767", ChannelEvent.TYPE_UNKNOWN, now)

        list_url = reverse('channels.channelevent_calls')

        response = self.fetch_protected(list_url, self.user)

        self.assertEqual(response.context['object_list'].count(), 2)
        self.assertContains(response, "Missed Incoming Call")
        self.assertContains(response, "Incoming Call (600 seconds)")


class SyncEventTest(SmartminTest):

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")
        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Temba", timezone="Africa/Kigali", created_by=self.user, modified_by=self.user)
        self.tel_channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                          secret="12345", gcm_id="123")

    def test_sync_event_model(self):
        self.sync_event = SyncEvent.create(self.tel_channel, dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI",
                                                                  pending=[1, 2], retry=[3, 4], cc='RW'), [1, 2])
        self.assertEqual(SyncEvent.objects.all().count(), 1)
        self.assertEqual(self.sync_event.get_pending_messages(), [1, 2])
        self.assertEqual(self.sync_event.get_retry_messages(), [3, 4])
        self.assertEqual(self.sync_event.incoming_command_count, 0)

        self.sync_event = SyncEvent.create(self.tel_channel, dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI",
                                                                  pending=[1, 2], retry=[3, 4], cc='US'), [1])
        self.assertEqual(self.sync_event.incoming_command_count, 0)
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)

        # we shouldn't update country once the relayer is claimed
        self.assertEqual('RW', self.tel_channel.country)


class ChannelAlertTest(TembaTest):

    def test_no_alert_email(self):
        # set our last seen to a while ago
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        check_channels_task()
        self.assertTrue(len(mail.outbox) == 0)

        # add alert email, remove org and set last seen to now to force an resolve email to try to send
        self.channel.alert_email = 'fred@unicef.org'
        self.channel.org = None
        self.channel.last_seen = timezone.now()
        self.channel.save()
        check_channels_task()

        self.assertTrue(len(mail.outbox) == 0)


class ChannelClaimTest(TembaTest):

    @override_settings(SEND_EMAILS=True)
    def test_disconnected_alert(self):
        # set our last seen to a while ago
        self.channel.alert_email = 'fred@unicef.org'
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        branding = copy.deepcopy(settings.BRANDING)
        branding['rapidpro.io']['from_email'] = 'support@mybrand.com'
        with self.settings(BRANDING=branding):
            check_channels_task()

            # should have created one alert
            alert = Alert.objects.get()
            self.assertEqual(self.channel, alert.channel)
            self.assertEqual(Alert.TYPE_DISCONNECTED, alert.alert_type)
            self.assertFalse(alert.ended_on)

            self.assertTrue(len(mail.outbox) == 1)
            template = 'channels/email/disconnected_alert.txt'
            context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                           branding=self.channel.org.get_branding(),
                           last_seen=self.channel.last_seen, sync=alert.sync_event)

            text_template = loader.get_template(template)
            text = text_template.render(context)

            self.assertEqual(mail.outbox[0].body, text)
            self.assertEqual(mail.outbox[0].from_email, 'support@mybrand.com')

        # call it again
        check_channels_task()

        # still only one alert
        self.assertEqual(1, Alert.objects.all().count())
        self.assertTrue(len(mail.outbox) == 1)

        # ok, let's have the channel show up again
        self.channel.last_seen = timezone.now() + timedelta(minutes=5)
        self.channel.save()

        check_channels_task()

        # still only one alert, but it is now ended
        alert = Alert.objects.get()
        self.assertTrue(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)
        template = 'channels/email/connected_alert.txt'
        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       branding=self.channel.org.get_branding(),
                       last_seen=self.channel.last_seen, sync=alert.sync_event)

        text_template = loader.get_template(template)
        text = text_template.render(context)

        self.assertEqual(mail.outbox[1].body, text)

    @override_settings(SEND_EMAILS=True)
    def test_sms_alert(self):
        contact = self.create_contact("John Doe", '123')

        # create a message from two hours ago
        one_hour_ago = timezone.now() - timedelta(hours=1)
        two_hours_ago = timezone.now() - timedelta(hours=2)
        three_hours_ago = timezone.now() - timedelta(hours=3)
        four_hours_ago = timezone.now() - timedelta(hours=4)
        five_hours_ago = timezone.now() - timedelta(hours=5)
        six_hours_ago = timezone.now() - timedelta(hours=6)

        msg1 = self.create_msg(text="Message One", contact=contact, created_on=five_hours_ago, status='Q')

        # make sure our channel has been seen recently
        self.channel.last_seen = timezone.now()
        self.channel.alert_email = 'fred@unicef.org'
        self.channel.org = self.org
        self.channel.save()

        # ok check on our channel
        check_channels_task()

        # we don't have  successfully sent message and we have an alert and only one
        self.assertEqual(Alert.objects.all().count(), 1)

        alert = Alert.objects.get()
        self.assertEqual(self.channel, alert.channel)
        self.assertEqual(Alert.TYPE_SMS, alert.alert_type)
        self.assertFalse(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 1)

        # let's end the alert
        alert = Alert.objects.all()[0]
        alert.ended_on = six_hours_ago
        alert.save()

        dany = self.create_contact("Dany Craig", "765")

        # let have a recent sent message
        sent_msg = self.create_msg(text="SENT Message", contact=dany, created_on=four_hours_ago, sent_on=one_hour_ago, status='D')

        # ok check on our channel
        check_channels_task()

        # if latest_sent_message is after our queued message no alert is created
        self.assertEqual(Alert.objects.all().count(), 1)

        # consider the sent message was sent before our queued msg
        sent_msg.sent_on = three_hours_ago
        sent_msg.save()

        msg1.delete()
        msg1 = self.create_msg(text="Message One", contact=contact, created_on=two_hours_ago, status='Q')

        # check our channel again
        check_channels_task()

        #  no new alert created because we sent one in the past hour
        self.assertEqual(Alert.objects.all().count(), 1)

        sent_msg.sent_on = six_hours_ago
        sent_msg.save()

        alert = Alert.objects.all()[0]
        alert.created_on = six_hours_ago
        alert.save()

        # check our channel again
        check_channels_task()

        # this time we have a new alert and should create only one
        self.assertEqual(Alert.objects.all().count(), 2)

        # get the alert which is not ended
        alert = Alert.objects.get(ended_on=None)
        self.assertEqual(self.channel, alert.channel)
        self.assertEqual(Alert.TYPE_SMS, alert.alert_type)
        self.assertFalse(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)

        # run again, nothing should change
        check_channels_task()

        alert = Alert.objects.get(ended_on=None)
        self.assertFalse(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)

        # fix our message
        msg1.status = 'D'
        msg1.save()

        # run again, our alert should end
        check_channels_task()

        # still only one alert though, and no new email sent, alert must not be ended before one hour
        alert = Alert.objects.all().latest('ended_on')
        self.assertTrue(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)


class ChannelCountTest(TembaTest):

    def assertDailyCount(self, channel, assert_count, count_type, day):
        calculated_count = ChannelCount.get_day_count(channel, count_type, day)
        self.assertEqual(assert_count, calculated_count)

    def test_daily_counts(self):
        # test that messages to test contacts aren't counted
        self.admin.set_org(self.org)
        test_contact = Contact.get_test_contact(self.admin)
        Msg.create_outgoing(self.org, self.admin, test_contact, "Test Message", channel=self.channel)

        # no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # real contact, but no channel
        Msg.create_incoming(None, 'tel:+250788111222', "Test Message", org=self.org)

        # still no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # incoming msg with a channel
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222', "Test Message", org=self.org)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # insert another
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222', "Test Message", org=self.org)
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # squash our counts
        squash_channelcounts()

        # same count
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # and only one channel count
        self.assertEqual(ChannelCount.objects.all().count(), 1)

        # deleting a message doesn't decrement the count
        msg.delete()
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # ok, test outgoing now
        real_contact, urn_obj = Contact.get_or_create(self.org, 'tel:+250788111222', user=self.admin)
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Message", channel=self.channel)
        log = ChannelLog.objects.create(channel=self.channel, msg=msg, description="Unable to send", is_error=True)

        # squash our counts
        squash_channelcounts()

        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())
        self.assertEqual(ChannelCount.objects.filter(count_type=ChannelCount.SUCCESS_LOG_TYPE).count(), 0)
        self.assertEqual(ChannelCount.objects.filter(count_type=ChannelCount.ERROR_LOG_TYPE).count(), 1)

        # delete our log, should decrement our count
        log.delete()
        self.assertEqual(0, self.channel.get_count([ChannelCount.ERROR_LOG_TYPE]))

        # deleting a message doesn't decrement the count
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # incoming IVR
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222',
                                  "Test Message", org=self.org, msg_type=IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # outgoing ivr
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Voice",
                                  channel=self.channel, msg_type=IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())


class ChannelLogTest(TembaTest):

    def test_channellog_views(self):
        self.contact = self.create_contact("Fred Jones", "+12067799191")
        self.create_secondary_org(100000)

        incoming_msg = Msg.create_incoming(self.channel, "tel:+12067799191", "incoming msg", contact=self.contact)
        self.assertEqual(self.contact, incoming_msg.contact)

        success_msg = Msg.create_outgoing(self.org, self.admin, self.contact, "success message", channel=self.channel)
        success_msg.status_delivered()

        self.assertIsNotNone(success_msg.sent_on)

        success_log = ChannelLog.objects.create(channel=self.channel, msg=success_msg, description="Successfully Sent", is_error=False)
        success_log.response = ""
        success_log.request = "POST https://foo.bar/send?msg=failed+message"
        success_log.save(update_fields=['request', 'response'])

        failed_msg = Msg.create_outgoing(self.org, self.admin, self.contact, "failed message", channel=self.channel)
        failed_log = ChannelLog.log_error(dict_to_struct('MockMsg', failed_msg.as_task_json()), "Error Sending")

        failed_log.response = json.dumps(dict(error="invalid credentials"))
        failed_log.request = "POST https://foo.bar/send?msg=failed+message"
        failed_log.save(update_fields=['request', 'response'])

        # can't see the view without logging in
        list_url = reverse('channels.channellog_list') + "?channel=%d" % self.channel.id
        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        read_url = reverse('channels.channellog_read', args=[failed_log.id])
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        # same if logged in as other admin
        self.login(self.admin2)

        list_url = reverse('channels.channellog_list') + "?channel=%d" % self.channel.id
        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        read_url = reverse('channels.channellog_read', args=[failed_log.id])
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        # login as real admin
        self.login(self.admin)

        # check our list page has both our channel logs
        response = self.client.get(list_url)
        self.assertContains(response, "Successfully Sent")
        self.assertContains(response, "Error Sending")

        # view failed alone
        response = self.client.get(read_url)
        self.assertContains(response, "failed+message")
        self.assertContains(response, "invalid credentials")

        # disconnect our msg
        failed_log.msg = None
        failed_log.save(update_fields=['msg'])
        response = self.client.get(read_url)
        self.assertContains(response, "failed+message")
        self.assertContains(response, "invalid credentials")

        # view success alone
        response = self.client.get(reverse('channels.channellog_read', args=[success_log.id]))
        self.assertContains(response, "Successfully Sent")

        self.assertEqual(1, self.channel.get_success_log_count())
        self.assertEqual(1, self.channel.get_error_log_count())

        # change our org to anonymous
        self.org.is_anon = True
        self.org.save()

        # should no longer be able to see read page
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        # but if our admin is a superuser they can
        self.admin.is_superuser = True
        self.admin.save()

        response = self.client.get(read_url)
        self.assertContains(response, "invalid credentials")


class MageHandlerTest(TembaTest):

    def setUp(self):
        super(MageHandlerTest, self).setUp()

        self.org.webhook = {"url": "http://fake.com/webhook.php"}
        self.org.webhook_events = ALL_EVENTS
        self.org.save()

        self.joe = self.create_contact("Joe", number="+250788383383")

        with ESMockWithScroll():
            self.dyn_group = self.create_group("Bobs", query="twitter has bobby81")

    def create_contact_like_mage(self, name, twitter):
        """
        Creates a contact as if it were created in Mage, i.e. no event/group triggering or cache updating
        """
        contact = Contact.objects.create(org=self.org, name=name, is_active=True, is_blocked=False,
                                         uuid=uuid.uuid4(), is_stopped=False,
                                         modified_by=self.user, created_by=self.user,
                                         modified_on=timezone.now(), created_on=timezone.now())
        urn = ContactURN.objects.create(org=self.org, contact=contact,
                                        identity="twitter:%s" % twitter, scheme="twitter", path=twitter, priority="90")
        return contact, urn

    def create_message_like_mage(self, text, contact, contact_urn=None):
        """
        Creates a message as it if were created in Mage, i.e. no topup decrementing or cache updating
        """
        if not contact_urn:
            contact_urn = contact.get_urn(TEL_SCHEME)
        return Msg.objects.create(org=self.org, text=text,
                                  direction=INCOMING, created_on=timezone.now(),
                                  channel=self.channel, contact=contact, contact_urn=contact_urn)

    @override_settings(MAGE_AUTH_TOKEN='abc123')
    def test_handle_message(self):
        url = reverse('handlers.mage_handler', args=['handle_message'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])
        self.assertEqual(1000, self.org.get_credits_remaining())

        msg = self.create_message_like_mage(text="Hello 1", contact=self.joe)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(1000, self.org.get_credits_remaining())

        # check that GET doesn't work
        response = self.client.get(url, dict(message_id=msg.pk), **headers)
        self.assertEqual(405, response.status_code)

        # check that POST does work
        response = self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        self.assertEqual(200, response.status_code)

        # check that new message is handled and has a topup
        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        # check for a web hook event
        event = WebHookEvent.objects.get(org=self.org, event=WebHookEvent.TYPE_SMS_RECEIVED).data
        self.assertEqual(msg.id, event['sms'])

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(999, self.org.get_credits_remaining())

        # check that a message that has a topup, doesn't decrement twice
        msg = self.create_message_like_mage(text="Hello 2", contact=self.joe)
        (msg.topup_id, amount) = self.org.decrement_credit()
        msg.save()

        self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(2, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(998, self.org.get_credits_remaining())

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")
        msg = self.create_message_like_mage(text="Hello via Mage", contact=mage_contact, contact_urn=mage_contact_urn)

        response = self.client.post(url, dict(message_id=msg.pk, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)

        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(3, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(997, self.org.get_credits_remaining())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check invalid auth key
        response = self.client.post(url, dict(message_id=msg.pk), **dict(HTTP_AUTHORIZATION='Token xyz'))
        self.assertEqual(401, response.status_code)

        # check rejection of empty or invalid msgId
        response = self.client.post(url, dict(), **headers)
        self.assertEqual(400, response.status_code)
        response = self.client.post(url, dict(message_id='xx'), **headers)
        self.assertEqual(400, response.status_code)

    @override_settings(MAGE_AUTH_TOKEN='abc123')
    def test_follow_notification(self):
        url = reverse('handlers.mage_handler', args=['follow_notification'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)

        flow = self.create_flow()

        channel = Channel.create(self.org, self.user, None, 'TT', "Twitter Channel", address="billy_bob")

        Trigger.objects.create(created_by=self.user, modified_by=self.user, org=self.org,
                               trigger_type=Trigger.TYPE_FOLLOW, flow=flow, channel=channel)

        contact = self.create_contact("Mary Jo", twitter='mary_jo')
        urn = contact.get_urn(TWITTER_SCHEME)

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, flow.runs.all().count())
        self.assertTrue(ChannelEvent.objects.filter(channel=channel, contact=contact,
                                                    event_type=ChannelEvent.TYPE_FOLLOW))

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        # simulate a a follow from existing stopped contact
        contact.stop(self.admin)

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, flow.runs.all().count())

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        contact.refresh_from_db()
        self.assertFalse(contact.is_stopped)

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")

        response = self.client.post(url, dict(channel_id=channel.id,
                                              contact_urn_id=mage_contact_urn.id, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, flow.runs.all().count())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check contact count updated
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts[ContactGroup.TYPE_ALL], 3)

        # simulate the follow of a released channel
        channel_events_count = ChannelEvent.objects.filter(channel=channel).count()
        channel.release()

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(ChannelEvent.objects.filter(channel=channel).count(), channel_events_count)

    @override_settings(MAGE_AUTH_TOKEN='abc123')
    def test_stop_contact(self):
        url = reverse('handlers.mage_handler', args=['stop_contact'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)
        contact = self.create_contact("Mary Jo", twitter='mary_jo')

        response = self.client.post(url, dict(contact_id=contact.id), **headers)
        self.assertEqual(200, response.status_code)

        # check the contact got stopped
        contact.refresh_from_db()
        self.assertTrue(contact.is_stopped)

        # try with invalid id
        response = self.client.post(url, dict(contact_id=-1), **headers)

        # should get a 401
        self.assertEqual(400, response.status_code)


class JunebugTestMixin(object):

    def mk_event(self, **kwargs):
        default = {
            'event_type': 'submitted',
            'message_id': 'message-id',
            'timestamp': '2017-01-01 00:00:00+0000',
        }
        default.update(kwargs)
        return default

    def mk_ussd_msg(self, session_event='new', session_id=None, **kwargs):
        return self.mk_msg(
            channel_data={'session_event': session_event,
                          'session_id': session_id}, **kwargs)

    def mk_msg(self, **kwargs):
        default = {
            "channel_data": {},
            "from": "+27123456789",
            "channel_id": "channel-id",
            "timestamp": "2017-01-01 00:00:00.00",
            "content": "content",
            "to": "to-addr",
            "reply_to": None,
            "message_id": "message-id"
        }
        default.update(kwargs)
        return default


class JunebugTest(JunebugTestMixin, TembaTest):

    def setUp(self):
        super(JunebugTest, self).setUp()

        self.channel.delete()

        self.channel = Channel.create(
            self.org, self.user, 'RW', 'JNU', None, '1234',
            config=dict(username='junebug-user', password='junebug-pass', send_url='http://example.org/'),
            uuid='00000000-0000-0000-0000-000000001234',
            role=Channel.DEFAULT_ROLE)

    def tearDown(self):
        super(JunebugTest, self).tearDown()

    def test_get_request(self):
        response = self.client.get(
            reverse('handlers.junebug_handler',
                    args=['event', self.channel.uuid]))
        self.assertEqual(response.status_code, 400)

    def test_status_with_invalid_event(self):
        delivery_url = reverse('handlers.junebug_handler',
                               args=['event', self.channel.uuid])
        response = self.client.post(delivery_url, data=json.dumps({}),
                                    content_type='application/json')
        self.assertContains(response, 'Missing one of', status_code=400)

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = self.mk_event()
        response = self.client.post(
            reverse('handlers.junebug_handler',
                    args=['event', 'not-real-uuid']),
            data=json.dumps(data),
            content_type='application/json')
        self.assertEqual(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.junebug_handler',
                               args=['event', self.channel.uuid])
        response = self.client.post(delivery_url, data=json.dumps(data),
                                    content_type='application/json')
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = data['message_id']
        msg.save(update_fields=('external_id',))

        # data['id'] = msg.external_id

        def assertStatus(sms, event_type, assert_status):
            data['event_type'] = event_type
            response = self.client.post(
                reverse('handlers.junebug_handler',
                        args=['event', self.channel.uuid]),
                data=json.dumps(data),
                content_type='application/json')
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'submitted', SENT)
        assertStatus(msg, 'delivery_succeeded', DELIVERED)
        assertStatus(msg, 'delivery_failed', FAILED)
        assertStatus(msg, 'rejected', FAILED)

    def test_status_invalid_message_id(self):
        # ok, what happens with an invalid uuid?
        data = self.mk_event()
        response = self.client.post(
            reverse('handlers.junebug_handler',
                    args=['event', self.channel.uuid]),
            data=json.dumps(data),
            content_type='application/json')
        self.assertContains(
            response,
            "Message with external id of '%s' not found" % (
                data['message_id'],), status_code=400)

    def test_status_with_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        data = self.mk_event()
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = data['message_id']
        msg.save(update_fields=('external_id',))

        def assertStatus(sms, event_type, assert_status):
            data['event_type'] = event_type
            response = self.client.post(
                reverse('handlers.junebug_handler',
                        args=['event', self.channel.uuid]),
                data=json.dumps(data),
                content_type='application/json',
                HTTP_AUTHORIZATION="Token UjOq8ATo2PDS6L08t6vlqSoK")
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'submitted', SENT)
        assertStatus(msg, 'delivery_succeeded', DELIVERED)
        assertStatus(msg, 'delivery_failed', FAILED)
        assertStatus(msg, 'rejected', FAILED)

    def test_status_incorrect_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        # ok, what happens with an invalid uuid?
        data = self.mk_event()
        response = self.client.post(
            reverse('handlers.junebug_handler',
                    args=['event', self.channel.uuid]),
            data=json.dumps(data),
            content_type='application/json',
            HTTP_AUTHORIZATION="Token Not_token")
        self.assertEqual(401, response.status_code)

    def test_receive_with_invalid_message(self):
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps({}),
                                    content_type='application/json')
        self.assertContains(response, 'Missing one of', status_code=400)

    def test_receive(self):
        data = self.mk_msg(content="événement")
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ack')

        # load our message
        msg = Msg.objects.get()
        self.assertEqual(data["from"], msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("événement", msg.text)

    def test_receive_with_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        data = self.mk_msg(content="événement")
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json',
                                    HTTP_AUTHORIZATION="Token UjOq8ATo2PDS6L08t6vlqSoK")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ack')

        # load our message
        msg = Msg.objects.get()
        self.assertEqual(data["from"], msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("événement", msg.text)

    def test_receive_with_incorrect_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        data = self.mk_msg(content="événement")
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json',
                                    HTTP_AUTHORIZATION="Token Not_token")

        self.assertEqual(response.status_code, 401)

    @override_settings(SEND_MESSAGES=True)
    def test_send_wired(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                }
            }))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(
                msg.external_id, '07033084-5cfd-4812-90a4-e4d24ffb6e3d')

            self.clear_cache()

            self.assertEqual(mock.call_args[1]['json']['content'], "événement")

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                }
            }))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(
                msg.external_id, '07033084-5cfd-4812-90a4-e4d24ffb6e3d')

            self.clear_cache()

            self.assertEqual(mock.call_args[1]['json']['content'], "MT\nhttps://example.com/attachments/pic.jpg")

    @override_settings(SEND_MESSAGES=True)
    def test_send_errored_remote(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(499, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

    @override_settings(SEND_MESSAGES=True)
    def test_send_errored_exception(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            # force an exception
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_deal_with_unexpected_response(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'unexpected': 'unpleasant surprise',
                }
            }))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            self.assertTrue(ChannelLog.objects.filter(description__icontains="Unable to read external message_id"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_adds_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                }
            }))

            # manually send it off
            self.channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            self.assertEqual(mock.call_args[1]['json']['event_auth_token'],
                             "UjOq8ATo2PDS6L08t6vlqSoK")


class FacebookWhitelistTest(TembaTest):

    def setUp(self):
        super(FacebookWhitelistTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'FB', None, '1234',
                                      config={Channel.CONFIG_AUTH_TOKEN: 'auth'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_whitelist(self):
        whitelist_url = reverse('channels.channel_facebook_whitelist', args=[self.channel.uuid])
        response = self.client.get(whitelist_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_read', args=[self.channel.uuid]))

        self.assertContains(response, whitelist_url)

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, '{"error": { "message": "FB Error" } }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain='https://foo.bar'))
            self.assertFormError(response, 'form', None, 'FB Error')

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "ok": "true" }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain='https://foo.bar'))

            mock.assert_called_once_with('https://graph.facebook.com/v2.12/me/thread_settings?access_token=auth',
                                         json=dict(setting_type='domain_whitelisting',
                                                   whitelisted_domains=['https://foo.bar'],
                                                   domain_action_type='add'))

            self.assertNoFormErrors(response)


class CourierTest(TembaTest):
    def test_queue_to_courier(self):
        self.channel.channel_type = 'T'
        self.channel.save()

        bob = self.create_contact("Bob", urn='tel:+12065551111')
        incoming = self.create_msg(contact=bob, text="Hello", direction="I", external_id="external-id")

        # create some outgoing messages for our channel
        msg1 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065551111', "Outgoing 1",
                                   attachments=['image/jpg:https://example.com/test.jpg', 'image/jpg:https://example.com/test2.jpg'])
        msg2 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065552222', "Outgoing 2", response_to=incoming,
                                   attachments=[])
        msg3 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065553333', "Outgoing 3", high_priority=False,
                                   attachments=None)
        msg4 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065554444', "Outgoing 4", high_priority=True)
        msg5 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065554444', "Outgoing 5", high_priority=True)
        all_msgs = [msg1, msg2, msg3, msg4, msg5]

        Msg.send_messages(all_msgs)

        # we should have been queued to our courier queues and our msgs should be marked as such
        for msg in all_msgs:
            msg.refresh_from_db()
            self.assertEqual(msg.status, QUEUED)

        self.assertFalse(msg1.high_priority)

        # responses arent enough to be high priority, it depends on run responded
        self.assertFalse(msg2.high_priority)

        self.assertFalse(msg3.high_priority)
        self.assertTrue(msg4.high_priority)  # explicitly high
        self.assertTrue(msg5.high_priority)

        # check against redis
        r = get_redis_connection()

        # should have our channel in the active queue
        queue_name = "msgs:" + self.channel.uuid + "|10"
        self.assertEqual(1, r.zcard("msgs:active"))
        self.assertEqual(0, r.zrank("msgs:active", queue_name))

        # check that messages went into the correct queues
        high_priority_msgs = [json.loads(force_text(t)) for t in r.zrange(queue_name + "/1", 0, -1)]
        low_priority_msgs = [json.loads(force_text(t)) for t in r.zrange(queue_name + "/0", 0, -1)]

        self.assertEqual([[m['text'] for m in b] for b in high_priority_msgs], [["Outgoing 4", "Outgoing 5"]])
        self.assertEqual([[m['text'] for m in b] for b in low_priority_msgs], [["Outgoing 1"], ["Outgoing 2"], ["Outgoing 3"]])

        self.assertEqual(low_priority_msgs[0][0]['attachments'], ['image/jpg:https://example.com/test.jpg', 'image/jpg:https://example.com/test2.jpg'])
        self.assertEqual(low_priority_msgs[0][0]['tps_cost'], 2)
        self.assertIsNone(low_priority_msgs[1][0]['attachments'])
        self.assertEqual(low_priority_msgs[1][0]['tps_cost'], 1)
        self.assertEqual(low_priority_msgs[1][0]['response_to_external_id'], "external-id")
        self.assertIsNone(low_priority_msgs[2][0]['attachments'])


class HandleEventTest(TembaTest):
    def test_new_conversation_channel_event(self):
        self.joe = self.create_contact("Joe", "+12065551212")
        flow = self.get_flow('favorites')
        Trigger.create(self.org, self.admin, Trigger.TYPE_NEW_CONVERSATION, flow)

        event = ChannelEvent.create(self.channel, "tel:+12065551212", ChannelEvent.TYPE_NEW_CONVERSATION, timezone.now())
        push_task(self.org, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=CHANNEL_EVENT, event_id=event.id))

        # should have been started in our flow
        self.assertTrue(FlowRun.objects.filter(flow=flow, contact=self.joe))

    def test_stop_contact_channel_event(self):
        self.joe = self.create_contact("Joe", "+12065551212")

        self.assertFalse(self.joe.is_stopped)
        event = ChannelEvent.create(self.channel, "tel:+12065551212", ChannelEvent.TYPE_STOP_CONTACT, timezone.now())
        push_task(self.org, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=CHANNEL_EVENT, event_id=event.id))

        self.joe.refresh_from_db()
        self.assertTrue(self.joe.is_stopped)


class TwitterTest(TembaTest):

    def setUp(self):
        super(TwitterTest, self).setUp()

        self.channel.delete()

        # an old style Twitter channel which would use Mage for receiving messages
        self.twitter = Channel.create(self.org, self.user, None, 'TT', None, 'billy_bob',
                                      config={'oauth_token': 'abcdefghijklmnopqrstuvwxyz', 'oauth_token_secret': '0123456789'},
                                      uuid='00000000-0000-0000-0000-000000002345')

        self.joe = self.create_contact("Joe", twitterid='10002')

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        msg = self.joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('twython.Twython.send_direct_message') as mock:
            mock.return_value = dict(id=1234567890)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we were only called once
            self.assertEqual(1, mock.call_count)

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertEqual('1234567890', msg.external_id)
            self.assertTrue(msg.sent_on)
            self.assertEqual(mock.call_args[1]['text'], "MT\nhttps://example.com/attachments/pic.jpg")

            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        testers = self.create_group("Testers", [self.joe])

        msg = self.joe.send("This is a long message, longer than just 160 characters, it spans what was before "
                            "more than one message but which is now but one, solitary message, going off into the "
                            "Twitterverse to tweet away.",
                            self.admin, trigger_send=False)[0]

        with patch('requests.sessions.Session.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(id=1234567890)))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we were only called once
            self.assertEqual(1, mock.call_count)
            self.assertEqual("10002", mock.call_args[1]['data']['user_id'])

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertEqual('1234567890', msg.external_id)
            self.assertTrue(msg.sent_on)
            self.assertEqual(mock.call_args[1]['data']['text'], msg.text)

            self.clear_cache()

        ChannelLog.objects.all().delete()

        msg.contact_urn.path = "joe81"
        msg.contact_urn.scheme = 'twitter'
        msg.contact_urn.display = None
        msg.contact_urn.identity = "twitter:joe81"
        msg.contact_urn.save()

        with patch('requests.sessions.Session.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(id=1234567890)))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we were only called once
            self.assertEqual(1, mock.call_count)
            self.assertEqual("joe81", mock.call_args[1]['data']['screen_name'])

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertEqual('1234567890', msg.external_id)
            self.assertTrue(msg.sent_on)
            self.assertEqual(mock.call_args[1]['data']['text'], msg.text)

            self.clear_cache()

        ChannelLog.objects.all().delete()

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("Failed to send message")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)
            self.assertEqual("Failed to send message", ChannelLog.objects.get(msg=msg).description)

            self.clear_cache()

        ChannelLog.objects.all().delete()

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("Different 403 error.", error_code=403)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # should not fail the contact
            contact = Contact.objects.get(id=self.joe.id)
            self.assertFalse(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 1)

            # should record the right error
            self.assertTrue(ChannelLog.objects.get(msg=msg).description.find("Different 403 error") >= 0)

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("You cannot send messages to users who are not following you.",
                                            error_code=403)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should fail the message
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)

            # should be stopped
            contact = Contact.objects.get(id=self.joe.id)
            self.assertTrue(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 0)

            self.clear_cache()

        self.joe.is_stopped = False
        self.joe.save(update_fields=('is_stopped',))
        testers.update_contacts(self.user, [self.joe], add=True)

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("There was an error sending your message: You can't send direct messages to this user right now.",
                                            error_code=403)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should fail the message
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)

            # should fail the contact permanently (i.e. removed from groups)
            contact = Contact.objects.get(id=self.joe.id)
            self.assertTrue(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 0)

            self.clear_cache()

        self.joe.is_stopped = False
        self.joe.save(update_fields=('is_stopped',))
        testers.update_contacts(self.user, [self.joe], add=True)

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("Sorry, that page does not exist.", error_code=404)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should fail the message
            msg.refresh_from_db()
            self.assertEqual(msg.status, FAILED)
            self.assertEqual(msg.error_count, 2)

            # should fail the contact permanently (i.e. removed from groups)
            contact = Contact.objects.get(id=self.joe.id)
            self.assertTrue(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 0)

            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send_quick_replies(self):
        quick_replies = ['Yes', 'No']
        msg = self.joe.send("Hello, world!", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with patch('requests.sessions.Session.post') as mock:
            response_dict = {
                "event": {
                    "created_timestamp": "1504717797522",
                    "message_create": {
                        "message_data": {
                            "text": "Hello, choose\u200b an option, please.",
                            "quick_reply": {
                                "type": "options",
                                "options": [{
                                    "label": "Yes"
                                }, {
                                    "label": "No"
                                }]
                            },
                            "entities": {
                                "symbols": [],
                                "user_mentions": [],
                                "hashtags": [],
                                "urls": []
                            }
                        },
                        "sender_id": "000000",
                        "target": {
                            "recipient_id": "10002"
                        }
                    },
                    "type": "message_create",
                    "id": "000000000000000000"
                }
            }
            mock.return_value = MockResponse(200, json.dumps(response_dict))

            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            data = json.dumps(dict(event=dict(message_create=dict(message_data=dict(
                text='Hello, world!',
                quick_reply=dict(
                    type='options',
                    options=[dict(label='Yes'), dict(label='No')]
                )),
                target=dict(recipient_id='10002')),
                type='message_create')
            ))

            mock.assert_called_with('https://api.twitter.com/1.1/direct_messages/events/new.json',
                                    files=None,
                                    data=ANY)

            args, kwargs = mock.call_args
            six.assertCountEqual(self, data, kwargs.get('data'))

            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "000000000000000000")
            self.assertEqual(msg.metadata, dict(quick_replies=quick_replies))
            data_args = json.loads(mock.call_args[1]['data'])
            message_data = data_args['event']['message_create']['message_data']
            self.assertEqual(message_data['quick_reply']['options'][0]['label'], 'Yes')
            self.assertEqual(message_data['quick_reply']['options'][1]['label'], 'No')
            self.clear_cache()


class FacebookTest(TembaTest):

    def setUp(self):
        super(FacebookTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'FB', None, '1234',
                                      config={Channel.CONFIG_AUTH_TOKEN: 'auth'},
                                      uuid='00000000-0000-0000-0000-000000001234')

        (self.contact, self.urn) = Contact.get_or_create(org=self.org, urn='facebook:1122', channel=self.channel)

    def tearDown(self):
        super(FacebookTest, self).tearDown()

    def test_referrals_optin(self):
        # create two triggers for referrals
        flow = self.get_flow('favorites')
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='join',
                               flow=flow, created_by=self.admin, modified_by=self.admin)
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='signup',
                               flow=flow, created_by=self.admin, modified_by=self.admin)

        event = ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            contact=self.contact,
            event_type=ChannelEvent.TYPE_REFERRAL,
            extra={"referrer_id": "join"},
            occurred_on=timezone.now(),
        )
        event.handle()

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("What is your favorite color?", contact1.msgs.all().first().text)

    def test_referrals_params(self):
        # create two triggers for referrals
        favorites = self.get_flow('favorites')
        pick = self.get_flow('pick_a_number')

        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='join',
                               flow=favorites, created_by=self.admin, modified_by=self.admin)
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='signup',
                               flow=favorites, created_by=self.admin, modified_by=self.admin)
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='',
                               flow=pick, created_by=self.admin, modified_by=self.admin)

        event = ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            contact=self.contact,
            event_type=ChannelEvent.TYPE_REFERRAL,
            extra={"referrer_id": "JOIN", "source": "SHORTLINK", "type": "OPEN_THREAD"},
            occurred_on=timezone.now(),
        )
        event.handle()

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("What is your favorite color?", contact1.msgs.order_by('id').last().text)

        event = ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            contact=self.contact,
            event_type=ChannelEvent.TYPE_REFERRAL,
            extra={"referrer_id": "not_handled", "source": "SHORTLINK", "type": "OPEN_THREAD"},
            occurred_on=timezone.now(),
        )
        event.handle()

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("Pick a number between 1-10.", contact1.msgs.order_by('id').last().text)

        event = ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            contact=self.contact,
            event_type=ChannelEvent.TYPE_REFERRAL,
            extra={"referrer_id": "signup", "source": "SHORTLINK", "type": "OPEN_THREAD"},
            occurred_on=timezone.now(),
        )
        event.handle()

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("What is your favorite color?", contact1.msgs.all().first().text)

        # and that we created an event for it
        self.assertTrue(ChannelEvent.objects.filter(contact=contact1, event_type=ChannelEvent.TYPE_REFERRAL))
