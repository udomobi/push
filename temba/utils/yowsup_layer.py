import requests
from yowsup.layers.interface import YowInterfaceLayer, ProtocolEntityCallback
from yowsup.layers.protocol_media.layer import YowMediaProtocolLayer
from yowsup.layers.protocol_media.mediauploader import MediaUploader
from yowsup.layers.protocol_media.protocolentities.iq_requestupload import RequestUploadIqProtocolEntity
from yowsup.layers.protocol_media.protocolentities.message_media_downloadable_image import \
    ImageDownloadableMediaMessageProtocolEntity
from yowsup.layers.protocol_media.protocolentities.message_media_location import LocationMediaMessageProtocolEntity
from yowsup.layers.protocol_messages.protocolentities import TextMessageProtocolEntity
from yowsup.stacks import YowStack
from yowsup.layers import YowLayerEvent
from yowsup.layers.auth import YowCryptLayer, YowAuthenticationProtocolLayer, AuthError
from yowsup.layers.coder import YowCoderLayer
from yowsup.layers.network import YowNetworkLayer, YowNetworkNoProxyLayer
from yowsup.layers.protocol_messages import YowMessagesProtocolLayer
from yowsup.layers.stanzaregulator import YowStanzaRegulator
from yowsup.layers.protocol_receipts import YowReceiptProtocolLayer
from yowsup.layers.protocol_acks import YowAckProtocolLayer
from yowsup.layers.logger import YowLoggerLayer
from yowsup.layers import YowParallelLayer
import threading
import logging
import re
import os

logger = logging.getLogger(__name__)


class SendLayer(YowInterfaceLayer):
    # This message is going to be replaced by the @param message in YowsupSendStack construction
    # i.e. list of (jid, message) tuples
    PROP_MESSAGES = "org.openwhatsapp.yowsup.prop.sendclient.queue"

    def __init__(self):
        super(SendLayer, self).__init__()
        self.ackQueue = []
        self.lock = threading.Condition()

    # call back function when there is a successful connection to whatsapp server
    @ProtocolEntityCallback("success")
    def onSuccess(self, successProtocolEntity):
        self.lock.acquire()
        for target in self.getProp(self.__class__.PROP_MESSAGES, []):
            # getProp() is trying to retreive the list of (jid, message) tuples, if none exist, use the default []
            phone, message = target
            location_expression = re.compile('^(\-?\d+(\.\d+)?),\s*(\-?\d+(\.\d+)?)$')
            image_expression = re.compile('(https?:\/\/.*\.(?:png|jpg|jpeg))')
            if location_expression.match(message):
                messageEntity = LocationMediaMessageProtocolEntity(message.split(',')[0], message.split(',')[1],
                                                                   None, None, 'raw', to=self.to_jid(phone))
            elif image_expression.match(message):
                filepath = self.save_image_from_url(message)
                messageEntity = self.image_send(phone, filepath)
            else:
                messageEntity = TextMessageProtocolEntity(message, to=self.to_jid(phone))

            # append the id of message to ackQueue list
            # which the id of message will be deleted when ack is received.
            if messageEntity:
                self.ackQueue.append(messageEntity.getId())
                self.toLower(messageEntity)
        self.lock.release()

    # after receiving the message from the target number, target number will send a ack to sender(us)
    @ProtocolEntityCallback("ack")
    def onAck(self, entity):
        self.lock.acquire()
        # if the id match the id in ackQueue, then pop the id of the message out
        if entity.getId() in self.ackQueue:
            self.ackQueue.pop(self.ackQueue.index(entity.getId()))

        if not len(self.ackQueue):
            self.lock.release()
            logger.info("Message sent")
            raise KeyboardInterrupt()

        self.lock.release()

    def to_jid(self, phone):
        phone = ("%s" % phone).replace('+', '')
        if '@' in phone:
            phone = phone
        elif '-' in phone:
            phone = "%s@g.us" % phone
        else:
            phone = "%s@s.whatsapp.net" % phone

        return phone

    def requestImageUpload(self, imagePath, phone):
        self.demoContactJid = phone  # only for the sake of simplicity of example, shoudn't do this
        self.filePath = imagePath  # only for the sake of simplicity of example, shoudn't do this
        requestUploadEntity = RequestUploadIqProtocolEntity("image", filePath=imagePath)
        self._sendIq(requestUploadEntity, self.onRequestUploadResult, self.onRequestUploadError)

    def doSendImage(self, filePath, url, to, ip=None, caption=None):
        entity = ImageDownloadableMediaMessageProtocolEntity.fromFilePath(filePath, url, ip, to, caption=caption)
        self.toLower(entity)

    def onRequestUploadResult(self, jid, filePath, resultRequestUploadIqProtocolEntity, requestUploadIqProtocolEntity,
                              caption=None):

        doSendFn = self.doSendImage

        if resultRequestUploadIqProtocolEntity.isDuplicate():
            doSendFn(filePath, resultRequestUploadIqProtocolEntity.getUrl(), jid,
                     resultRequestUploadIqProtocolEntity.getIp(), caption)
        else:
            successFn = lambda filePath, jid, url: doSendFn(filePath, url, jid,
                                                            resultRequestUploadIqProtocolEntity.getIp(), caption)
            mediaUploader = MediaUploader(jid, self.getOwnJid(), filePath,
                                          resultRequestUploadIqProtocolEntity.getUrl(),
                                          resultRequestUploadIqProtocolEntity.getResumeOffset(),
                                          successFn, self.onUploadError, None, async=False)
            mediaUploader.start()

        os.remove(filePath)

    def onRequestUploadError(self, jid, path, errorRequestUploadIqProtocolEntity, requestUploadIqProtocolEntity):
        print "Request upload for file %s for %s failed" % (path, jid)

    def onUploadSuccess(self, filePath, jid, url):
        # convenience method to detect file/image attributes for sending, requires existence of 'pillow' library
        entity = ImageDownloadableMediaMessageProtocolEntity.fromFilePath(filePath, url, '', jid)
        self.toLower(entity)

    def onUploadError(self, filePath, jid, url):
        print "Upload file %s to %s for %s failed!" % (filePath, url, jid)

    def save_image_from_url(self, url):
        """
        Save remote images from url to image field.
        Requires python-requests
        """

        from django.core.files.temp import NamedTemporaryFile

        request = requests.get(url)

        if request.status_code == 200:
            file_url, file_ext = os.path.splitext(request.url)
            img_temp = NamedTemporaryFile(prefix='whatsapp_file_', suffix=file_ext, dir='/tmp', delete=False)
            img_temp.write(request.content)
            img_temp.flush()

            with open(img_temp.name, 'wb') as f:
                for chunk in request.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
                        f.flush()

                return f.name
        else:
            return False

    def image_send(self, number, path, caption=None):
        jid = self.to_jid(number)
        entity = RequestUploadIqProtocolEntity(RequestUploadIqProtocolEntity.MEDIA_TYPE_IMAGE, filePath=path)
        successFn = lambda successEntity, originalEntity: self.onRequestUploadResult(jid, path, successEntity,
                                                                                     originalEntity, caption)
        errorFn = lambda errorEntity, originalEntity: self.onRequestUploadError(jid, path, errorEntity, originalEntity)

        self._sendIq(entity, successFn, errorFn)


class YowsupSendStack(object):
    def __init__(self, credentials, messages, encryptionEnabled=False):
        """
        :param credentials:
        :param messages: list of (jid, message) tuples
        :param encryptionEnabled:
        :return:
        """
        if encryptionEnabled:
            from yowsup.layers.axolotl import YowAxolotlLayer
            layers = (
                SendLayer,
                YowParallelLayer([YowAuthenticationProtocolLayer, YowMessagesProtocolLayer, YowReceiptProtocolLayer,
                                  YowAckProtocolLayer, YowMediaProtocolLayer]),
                YowAxolotlLayer,
                YowLoggerLayer,
                YowCoderLayer,
                YowCryptLayer,
                YowStanzaRegulator,
                YowNetworkNoProxyLayer
                # YowNetworkLayer
            )
        else:
            layers = (
                SendLayer,
                YowParallelLayer([YowAuthenticationProtocolLayer, YowMessagesProtocolLayer, YowReceiptProtocolLayer,
                                  YowAckProtocolLayer, YowMediaProtocolLayer]),
                YowLoggerLayer,
                YowCoderLayer,
                YowCryptLayer,
                YowStanzaRegulator,
                YowNetworkNoProxyLayer
                # YowNetworkLayer
            )

        self.stack = YowStack(layers)
        self.stack.setProp(SendLayer.PROP_MESSAGES, messages)
        self.stack.setProp(YowAuthenticationProtocolLayer.PROP_PASSIVE, True)
        self.stack.setCredentials(credentials)

    def start(self):
        self.stack.broadcastEvent(YowLayerEvent(YowNetworkLayer.EVENT_STATE_CONNECT))
        try:
            self.stack.loop()
        except AuthError as e:
            print("Authentication Error: %s" % e.message)
