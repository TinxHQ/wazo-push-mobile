# Copyright 2017-2019 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
import uuid
from pyfcm import FCMNotification
from pyfcm.errors import RetryAfterException
from apns2.client import APNsClient
from apns2 import errors as apns2_errors
from apns2.payload import Payload

from wazo_auth_client import Client as AuthClient
from wazo_webhookd.plugins.subscription.service import SubscriptionService
from wazo_webhookd.services.helpers import HookRetry


logging.basicConfig()
logger = logging.getLogger(__name__)


class Service:

    def load(self, dependencies):
        bus_consumer = dependencies['bus_consumer']
        self._config = dependencies['config']
        self.subscription_service = SubscriptionService(self._config)
        bus_consumer.subscribe_to_event_names(uuid=uuid.uuid4(),
                                              event_names=['auth_user_external_auth_added'],
                                              # tenant_uuid=None,
                                              user_uuid=None,
                                              wazo_uuid=None,
                                              callback=self.on_external_auth_added)
        bus_consumer.subscribe_to_event_names(uuid=uuid.uuid4(),
                                              event_names=['auth_user_external_auth_deleted'],
                                              # tenant_uuid=None,
                                              user_uuid=None,
                                              wazo_uuid=None,
                                              callback=self.on_external_auth_deleted)
        logger.info('Mobile push notification plugin is started')

    def on_external_auth_added(self, body, event):
        if body['data'].get('external_auth_name') == 'mobile':
            user_uuid = body['data']['user_uuid']
            # TODO(sileht): Should come with the event
            tenant_uuid = self.get_tenant_uuid(user_uuid)
            self.subscription_service.create({
                'name': ('Push notification mobile for user '
                         '{}/{}'.format(tenant_uuid, user_uuid)),
                'service': 'mobile',
                'events': [
                    'chatd_user_room_message_created',
                    'call_push_notification',
                    'user_voicemail_message_created'
                ],
                'events_user_uuid': user_uuid,
                # 'events_tenant_uuid': tenant_uuid,
                'owner_user_uuid': user_uuid,
                'owner_tenant_uuid': tenant_uuid,
                'config': {},
                'metadata': {'mobile': 'true'},
            })
            logger.info('User registered: %s/%s', tenant_uuid, user_uuid)

    def on_external_auth_deleted(self, body, event):
        if body['data'].get('external_auth_name') == 'mobile':
            user_uuid = body['data']['user_uuid']
            # TODO(sileht): Should come with the event
            tenant_uuid = self.get_tenant_uuid(user_uuid)
            subscriptions = self.subscription_service.list(
                owner_user_uuid=user_uuid,
                owner_tenant_uuids=[tenant_uuid],
                search_metadata={'mobile': 'true'},
            )
            for subscription in subscriptions:
                self.subscription_service.delete(subscription.uuid)
            logger.info('User unregistered: %s/%s', tenant_uuid, user_uuid)

    def get_tenant_uuid(self, user_uuid):
        auth = self.get_auth(self._config)
        return auth.users.get(user_uuid)["tenant_uuid"]

    @classmethod
    def get_auth(cls, config):
        auth_config = dict(config['auth'])
        # FIXME(sileht): Keep the certificate
        auth_config['verify_certificate'] = False
        auth = AuthClient(**auth_config)
        token = auth.token.new('wazo_user', expiration=3600)
        auth.set_token(token["token"])
        auth.username = None
        auth.password = None
        return auth

    @classmethod
    def get_external_token(cls, config, user_uuid):
        auth = cls.get_auth(config)
        token = auth.external.get('mobile', user_uuid)
        tenant_uuid = auth.users.get(user_uuid)['tenant_uuid']
        external_config = auth.external.get_config('mobile', tenant_uuid)

        if token["apns_token"]:
            external_config['ios_apns_cert'] = '/tmp/ios.pem'

            with open(external_config['ios_apns_cert'], 'w') as cert:
                cert.write(external_config['ios_apn_certificate'] + "\r\n")
                cert.write(external_config['ios_apn_private'])

        return (token, external_config)

    @classmethod
    def run(cls, task, config, subscription, event):
        user_uuid = subscription['events_user_uuid']
        # TODO(sileht): We should also filter on tenant_uuid
        # tenant_uuid = subscription.get('events_tenant_uuid')
        if (event['data'].get('user_uuid') == user_uuid
                # and event['data']['tenant_uuid'] == tenant_uuid
                and event['name'] in ['chatd_user_room_message_created']):
            return

        data, external_config = cls.get_external_token(config, user_uuid)
        token = data['token']
        apns_token = data['apns_token']
        push = PushNotification(token, apns_token, external_config)

        msg = None
        data = event.get('data')
        name = event.get('name')

        if name == 'user_voicemail_message_created':
            msg = dict(notification_type='voicemailReceived', items=data)

        if name == 'call_push_notification':
            msg = dict(notification_type='incomingCall', items=data)

        if name == 'chatd_user_room_message_created':
            msg = dict(notification_type='messageReceived', items=data)

        if msg:
            return push.send_notification(msg)


class PushNotification(object):

    def __init__(self, external_token, apns_token, external_config):
        self.token = external_token
        self.apns_token = apns_token
        self.external_config = external_config

    def send_notification(self, data):
        message_title = None
        message_body = None

        is_incoming_call = data.get('notification_type') == 'incomingCall'
        is_voicemail = data.get('notification_type') == 'voicemailReceived'
        is_message = data.get('notification_type') == 'messageReceived'

        if is_incoming_call:
            message_title = 'Incoming Call'
            message_body = 'From: {}'.format(data['items']['peer_caller_id_number'])
            channel_id = 'wazo-notification-call'

        if is_voicemail:
            message_title = 'New voicemail'
            message_body = 'From: {}'.format(data['items']['message']['caller_id_num'])
            channel_id = 'wazo-notification-voicemail'

        if is_message:
            message_title = data['items']['alias']
            message_body = data['items']['content']
            channel_id = 'wazo-notification-chat'

        if self.apns_token and channel_id == 'wazo-notification-call':
            try:
                return self._send_via_apn(self.apns_token, data)
            except (apns2_errors.ServiceUnavailable,
                    apns2_errors.InternalServerError) as e:
                raise HookRetry({"error": str(e)})
        else:
            try:
                return self._send_via_fcm(message_title, message_body, channel_id, data)
            except RetryAfterException as e:
                raise HookRetry({"error": str(e)})

    def _send_via_fcm(self, message_title, message_body, channel_id, data):
        push_service = FCMNotification(api_key=self.external_config['fcm_api_key'])

        if (message_title and message_body):

            if channel_id == 'wazo-notification-call':
                notification = push_service.notify_single_device(
                    registration_id=self.token,
                    data_message=data,
                    extra_notification_kwargs=dict(priority='high'))
            else:
                notification = push_service.notify_single_device(
                    registration_id=self.token,
                    message_title=message_title,
                    message_body=message_body,
                    badge=1,
                    extra_notification_kwargs=dict(android_channel_id=channel_id),
                    data_message=data)

            if notification.get('failure') != 0:
                logger.error('Error to send push notification: %s', notification)
            return notification

    def _send_via_apn(self, apns_token, data):
        payload = Payload(alert=data, sound="default", badge=1)
        client = APNsClient(self.external_config['ios_apns_cert'],
                            use_sandbox=self.external_config['is_sandbox'],
                            use_alternative_port=False)
        client.send_notification(apns_token, payload, 'io.wazo.songbird.voip')
