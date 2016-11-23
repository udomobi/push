from __future__ import absolute_import, unicode_literals

import time

from datetime import timedelta
from djcelery_transactions import task
from django.utils import timezone
from django_redis import get_redis_connection
from .models import CreditAlert, Invitation, Org, TopUpCredits


@task(track_started=True, name='send_invitation_email_task')
def send_invitation_email_task(invitation_id):
    invitation = Invitation.objects.get(pk=invitation_id)
    invitation.send_email()


@task(track_started=True, name='send_alert_email_task')
def send_alert_email_task(alert_id):
    alert = CreditAlert.objects.get(pk=alert_id)
    alert.send_email()


@task(track_started=True, name='check_credits_task')
def check_credits_task():
    CreditAlert.check_org_credits()


@task(track_started=True, name='calculate_credit_caches')
def calculate_credit_caches():
    """
    Repopulates the active topup and total credits for each organization
    that received messages in the past week.
    """
    # get all orgs that have sent a message in the past week
    last_week = timezone.now() - timedelta(days=7)

    # for every org that has sent a message in the past week
    for org in Org.objects.filter(msgs__created_on__gte=last_week).distinct('pk'):
        start = time.time()
        org._calculate_credit_caches()
        print " -- recalculated credits for %s in %0.2f seconds" % (org.name, time.time() - start)


@task(track_started=True, name="squash_topupcredits")
def squash_topupcredits():
    r = get_redis_connection()

    key = 'squash_topupcredits'
    if not r.get(key):
        with r.lock(key, timeout=900):
            TopUpCredits.squash_credits()


@task(track_started=True, name="check_billing_agreements")
def check_billing_agreements():
    import paypalrestsdk
    from temba import settings
    from paypalrestsdk import BillingAgreement
    from temba.orgs.models import OrderPayment, TopUp

    paypalrestsdk.configure(settings.PAYPAL_API)
    orders_payment = OrderPayment.objects.filter(is_active=True, billing_agreement_id__isnull=False)
    count_inactive = 0
    for agreement in orders_payment:
        topups = TopUp.objects.filter(org=agreement.org, user=agreement.created_by, created_on__month=timezone.now().month, created_on__year=timezone.now().year)
        billing_agreement = BillingAgreement.find(agreement.billing_agreement_id)
        if billing_agreement.state == 'Active' and not topups:
            expires_on = timezone.now() + timedelta(days=30)
            TopUp.create(user=agreement.created_by, price=agreement.value, credits=agreement.credits, expires_on=expires_on)
        else:
            count_inactive += 1

    print ("-- Billing agreements inactives: {count}".format(count=count_inactive))
