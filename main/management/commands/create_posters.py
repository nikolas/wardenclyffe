from django.core.management.base import BaseCommand
from main.models import *
import random

class Command(BaseCommand):
    args = ''
    help = ''
    
    def handle(self,*args,**kwargs):
        for video in Video.objects.all():
            if video.poster_set.all().count() > 0:
                continue
            if video.image_set.all().count() == 0:
                continue
            image = video.image_set.all()[random.randint(0,video.image_set.all().count() - 1)]
            p = Poster.objects.create(video=video,image=image)
