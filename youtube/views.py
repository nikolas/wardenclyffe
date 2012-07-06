# Create your views here.
from annoying.decorators import render_to
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, HttpResponse
from wardenclyffe.main.models import Video, Operation, Collection, File
import uuid
import wardenclyffe.main.tasks
import os
from django.conf import settings
from django.db import transaction
from django_statsd.clients import statsd
from simplejson import dumps


@transaction.commit_manually
@login_required
@render_to('main/youtube.html')
def youtube(request):
    if request.method == "POST":
        statsd.incr("youtube.youtube")
        tmpfilename = request.POST.get('tmpfilename', '')
        operations = []
        if tmpfilename.startswith(settings.TMP_DIR):
            # make db entry
            filename = os.path.basename(tmpfilename)
            vuuid = os.path.splitext(filename)[0]
            try:
                collection = Collection.objects.filter(title="Youtube")[0]
                v = Video.objects.create(
                    collection=collection,
                    title=request.POST.get(
                        "title",
                        ("youtube video uploaded by %s" %
                         request.user.username)),
                    creator=request.user.username,
                    description=request.POST.get("description", ""),
                    uuid=vuuid)
                source_file = File.objects.create(video=v,
                                                  label="source file",
                                                  filename=filename,
                                                  location_type='none')
                params = dict(tmpfilename=tmpfilename,
                              source_file_id=source_file.id)
                o = Operation.objects.create(uuid=uuid.uuid4(),
                                             video=v,
                                             action="extract metadata",
                                             status="enqueued",
                                             params=dumps(params),
                                             owner=request.user)
                operations.append((o.id, params))
                params = dict(tmpfilename=tmpfilename, filename=tmpfilename,
                              tahoe_base=settings.TAHOE_BASE)
                o = Operation.objects.create(uuid=uuid.uuid4(),
                                             video=v,
                                             action="save file to tahoe",
                                             status="enqueued",
                                             params=dumps(params),
                                             owner=request.user)
                operations.append((o.id, params))
                params = dict(tmpfilename=tmpfilename)
                o = Operation.objects.create(uuid=uuid.uuid4(),
                                             video=v,
                                             action="make images",
                                             status="enqueued",
                                             params=dumps(params),
                                             owner=request.user)
                operations.append((o.id, params))
                params = dict(tmpfilename=tmpfilename)

                o = Operation.objects.create(uuid=uuid.uuid4(),
                                             video=v,
                                             action="upload to youtube",
                                             status="enqueued",
                                             params=dumps(params),
                                             owner=request.user)
                operations.append((o.id, params))
            except:
                statsd.incr("youtube.youtube.failure")
                transaction.rollback()
                raise
            else:
                transaction.commit()
                for o, kwargs in operations:
                    wardenclyffe.main.tasks.process_operation.delay(o, kwargs)
                return HttpResponseRedirect("/youtube/done/")
        else:
            return HttpResponse("no tmpfilename parameter set")
    else:
        transaction.commit()
    return dict()


@render_to('main/youtube_done.html')
def youtube_done(request):
    return dict()
