from django.http import JsonResponse
from django.views import View
from django.utils.html import format_html
from django.shortcuts import reverse
from .mixins import TranscriptionDataValidationMixin, ReceiveTranscriptionMixin
from pytube import YouTube
from django.conf import settings
import requests
from django.middleware import csrf
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import json
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from notifications.signals import notify
from wagtail_transcription.models import Transcription

class ValidateTranscriptionDataView(TranscriptionDataValidationMixin, View):
    """
    Validate video_id, model_instance_str, check if transcription for same video is running
    If data valid return modal with video title, link, thumbnail, channel name and button to continue
    If data invalid return modal with appropriate error message
    """
    def post(self, request, *args, **kwargs):
        data = request.POST
        video_id = data.get('video_id')
        edit_url = data.get('edit_url')
        # this string allow to dynamically get any model instance
        model_instance_str = data.get('model_instance')
        transcription_field = data.get('transcription_field')
        transcription_field_id = data.get('transcription_field_id')
        field_name = data.get('field_name')
        # validate data
        is_data_valid, response_message, _ = self.data_validation(video_id, model_instance_str, transcription_field, transcription_field_id)
        response_message = self.format_response_message(video_id, edit_url, transcription_field, transcription_field_id, field_name, model_instance_str, is_data_valid, response_message)
        return JsonResponse(response_message)

    def format_response_message(self, video_id, edit_url, transcription_field, transcription_field_id, field_name, model_instance_str, is_data_valid, response_message):
        if not is_data_valid:
            message = format_html(f"""
                <h3 style="color: #842e3c; margin:0"><b>{response_message.get("message")}</b></h3>
            """)
            response_message['message'] = message
        else:
            audio_url, audio_duration = self.yt_audio_and_duration(video_id)
            video_title, video_thumbnail, channel_name = self.get_youtube_video_data(video_id)
            message = format_html(f"""
                <h3 style="color: #0c622e; font-weight:bold">Transcription process will take about {self.format_seconds(audio_duration//1.25)}</h3>
                <a href="https://www.youtube.com/watch?v={video_id}" target="_blank">
                    <div style="display: flex; background: #262626">
                        <img src="{video_thumbnail}" alt="{channel_name}-image">
                        <div style="padding-left: .5rem; padding-top: .5rem; height: fit-content;">
                            <p style="font-size:14px; line-height:1; margin:0; color: white;">{video_title}</p>
                            <p style="font-size:12px; color:#a3a3a3; margin: 0; margin-top: .25rem">{channel_name}</p>
                        </div>
                    </div>
                </a>
                <div style="display:flex; margin-top: 1rem; justify-content: right">
                    <form method="POST" action="{reverse('wagtail_transcription:request_transcription')}">
                        <input type="hidden" name="csrfmiddlewaretoken" value="{csrf.get_token(self.request)}">
                        <input type="hidden" name="video_id" value="{video_id}">
                        <input type="hidden" name="transcription_field" value="{transcription_field}">
                        <input type="hidden" name="transcription_field_id" value="{transcription_field_id}">
                        <input type="hidden" name="field_name" value="{field_name}">
                        <input type="hidden" name="audio_url" value="{audio_url}">
                        <input type="hidden" name="audio_duration" value="{audio_duration}">
                        <input type="hidden" name="edit_url" value="{edit_url}">
                        <input type="hidden" name="model_instance_str" value="{model_instance_str}">
                        <button class="continue_btn button action-save" action="button">Continue</button>
                    </form>
                </div>
            """)
            response_message['message'] = message

        return response_message

    def format_seconds(self, seconds):
        """
        Format seconds to user friendly format
        """
        hours, seconds = f"{int(seconds//3600)} hour{'s' if seconds//3600 > 1 else ''} " if seconds//3600 > 0 else '', seconds - ((seconds//3600 )* 3600)
        minutes, seconds = f"{int(seconds//60)} minute{'s' if seconds//60 > 1 else ''} " if seconds//60 > 0 else '', seconds - ((seconds//60 )* 60)
        seconds = f"{int(seconds)} second{'s' if seconds > 1 else ''}"
        return f"{hours} {minutes} {seconds}"
        
    def yt_audio_and_duration(self, video_id):
        yt = YouTube(f'https://www.youtube.com/watch?v={video_id}')
        audio_url = yt.streams.all()[0].url  # Get the URL of the video stream
        return audio_url, yt.length

    def get_youtube_video_data(self, video_id):
        # get title, thumbnail, author
        url = f'https://www.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={settings.YOUTUBE_DATA_API_KEY}'
        r = requests.get(url)
        snippet = r.json()['items'][0]['snippet']
        return snippet['title'], snippet['thumbnails']['default']['url'], snippet['channelTitle']

class RequestTranscriptionView(TranscriptionDataValidationMixin, View):

    api_token = settings.ASSEMBLY_API_TOKEN

    def post(self, request, *args, **kwargs):
        data = request.POST
        video_id = data.get('video_id')
        # this string allow to dynamically get any model instance
        model_instance_str = data.get('model_instance_str')
        transcription_field = data.get('transcription_field')
        field_name = data.get('field_name')
        edit_url = data.get("edit_url")

        # validate data
        is_data_valid, response_message, _ = self.data_validation(video_id, model_instance_str, transcription_field)
        if is_data_valid:
            # encode model_instance_str, transcription_field, field_name to base 64
            model_instance_str_b64 = urlsafe_base64_encode(force_bytes(model_instance_str))
            transcription_field_b64 = urlsafe_base64_encode(force_bytes(transcription_field))
            field_name_b64 = urlsafe_base64_encode(force_bytes(field_name))
            edit_url_b64 = urlsafe_base64_encode(force_bytes(edit_url))

            webhook_url = settings.BASE_URL + reverse('wagtail_transcription:receive_transcription', 
            kwargs={'m':model_instance_str_b64, 't':transcription_field_b64, 'f':field_name_b64, 'e':edit_url_b64, 'v':video_id, 'u': request.user.id})
            response = self.transcript_audio(data.get("audio_url"), webhook_url)
            if response.get('id') is not None:
                # create transcription with completed=False
                Transcription.objects.create(
                    title=f"auto_transcription-{video_id}",
                    video_id=video_id,
                )

        return JsonResponse(response_message)
 
    def transcript_audio(self, audio_url, webhook_url):
        # TRANSCRIPE UPLOADED FILE
        endpoint = "https://api.assemblyai.com/v2/transcript"
        json = {
            "audio_url": audio_url,
            "webhook_url": webhook_url,
        }
        headers = {
            "authorization": self.api_token,
            "content-type": "application/json",
        }

        r = requests.post(endpoint, json=json, headers=headers)
        response = r.json()
        return response

@method_decorator(csrf_exempt, name='dispatch') #this allows to receive post request without csrf protection
class ReceiveTranscriptionView(ReceiveTranscriptionMixin, View):
    api_token = settings.ASSEMBLY_API_TOKEN

    def post(self, request, m, f, t, e, v, u, *args, **kwargs):
        model_instance_str = force_str(urlsafe_base64_decode(m)) # get model-instance-str
        transcription_field = force_str(urlsafe_base64_decode(t)) # get transcription-field
        field_name = force_str(urlsafe_base64_decode(f)) # get field-name
        edit_url = force_str(urlsafe_base64_decode(e))
        video_id = v # get youtube video id
        user_id = int(u) # get user id

        try:
            request_body = json.loads(request.body.decode('utf-8'))
            status, transcript_id = request_body.get('status'), request_body.get('transcript_id')
        except Exception:
            status, transcript_id = None, None

        try:
            if status == 'completed' and transcript_id:
                transcription_response = self.get_transcription(transcript_id)
                words = transcription_response.get("words")
                transcription_phrases = self.get_transcription_devided_by_phrases(words)
                io_output = self.transcription_phrases_to_docx(transcription_phrases)
                transcription_document = self.add_docx_to_wagtail_docs(io_output, video_id)
                # add transcription_document to model_instance transcription field
                model_instance = self.get_model_instance(model_instance_str)
                setattr(model_instance, field_name, video_id)
                setattr(model_instance, transcription_field, transcription_document)
                model_instance.save()
                # send notification
                notification_message = self.get_notification_message(transcription_document=transcription_document, edit_url=edit_url, video_id=video_id)
            else:
                # If error delete uncompleted Transcription
                Transcription.objects.filter(video_id=video_id).delete()
                notification_message = self.get_notification_message(error=True, edit_url=edit_url, video_id=video_id)
        except Exception as e:
            print(e)
            # If error delete uncompleted Transcription
            Transcription.objects.filter(video_id=video_id).delete()
            notification_message = self.get_notification_message(error=True, edit_url=edit_url, video_id=video_id)

        # send notification
        notify.send(sender=self.get_user(user_id), recipient=self.get_user(user_id), verb="Message", description=notification_message)
        
        return JsonResponse({"type":"success"})