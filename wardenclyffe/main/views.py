# stdlib imports
import os
import uuid
import wardenclyffe.main.tasks as tasks

from angeldust import PCP
from annoying.decorators import render_to
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import Paginator, EmptyPage, InvalidPage
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.generic.base import View, TemplateView
from django.views.generic.detail import DetailView
from django.views.generic.edit import DeleteView
from django.views.generic.list import ListView
from django_statsd.clients import statsd
from json import dumps, loads
from taggit.models import Tag
from wardenclyffe.main.forms import AddServerForm
from wardenclyffe.main.forms import UploadVideoForm, AddCollectionForm
from wardenclyffe.main.models import Video, Operation, Collection, File
from wardenclyffe.main.models import Metadata, Image, Poster
from wardenclyffe.main.models import Server, CollectionWorkflow
from surelink.helpers import PROTECTION_OPTIONS
from surelink.helpers import AUTHTYPE_OPTIONS
from surelink import SureLink
from wardenclyffe.util import uuidparse
from wardenclyffe.util.mail import send_mediathread_received_mail


def is_staff(user):
    return user and not user.is_anonymous() and user.is_staff


def get_pcp_workflows():
    """ returns list of workflows and error message.

    if it succeeds, error message will be an empty string
    if it fails, workflows will be an empty list """
    error_message = ""
    try:
        p = PCP(settings.PCP_BASE_URL,
                settings.PCP_USERNAME,
                settings.PCP_PASSWORD)
        workflows = p.workflows()
    except Exception, e:
        error_message = str(e)
        workflows = []
    return (workflows, error_message)


class StaffMixin(object):
    @method_decorator(login_required)
    @method_decorator(user_passes_test(is_staff))
    def dispatch(self, *args, **kwargs):
        return super(StaffMixin, self).dispatch(*args, **kwargs)


class IndexView(StaffMixin, TemplateView):
    template_name = 'main/index.html'

    def get_context_data(self, *args, **kwargs):
        return dict(
            collection=Collection.objects.filter(
                active=True).order_by("title"),
            videos=Video.objects.all().order_by("-modified")[:20],
            operations=Operation.objects.all().order_by("-modified")[:20])


class DashboardView(StaffMixin, TemplateView):
    template_name = 'main/dashboard.html'

    def get_context_data(self, *args, **kwargs):
        submitted = self.request.GET.get('submitted', '') == '1'
        status_filters = dict()
        for (status, get_param) in [
            ("failed", 'status_filter_failed'),
            ("complete", 'status_filter_complete'),
            ("submitted", 'status_filter_submitted'),
            ("inprogress", 'status_filter_inprogress'),
            ("enqueued", 'status_filter_enqueued'),
        ]:
            status_filters[status] = self.request.GET.get(
                get_param, not submitted)

        user_filter = self.request.GET.get('user', '')
        collection_filter = int(self.request.GET.get('collection',
                                                     False) or '0')
        d = dict(
            all_collection=Collection.objects.all().order_by("title"),
            all_users=User.objects.all(),
            user_filter=user_filter,
            collection_filter=collection_filter,
            submitted=submitted,
        )
        d.update(status_filters)
        return d


class ReceivedView(View):
    def post(self, request):
        if 'title' not in request.POST:
            return HttpResponse("expecting a title")
        statsd.incr('main.received')
        title = request.POST.get('title', 'no title')
        ruuid = uuidparse(title)
        r = Operation.objects.filter(uuid=ruuid)
        if r.count() == 1:
            operation = r[0]

            if operation.video.is_mediathread_submit():
                send_mediathread_received_mail(operation.video.title,
                                               operation.owner.username)

        else:
            statsd.incr('main.received_failure')

        return HttpResponse("ok")


class UploadifyView(View):
    def post(self, request, *args, **kwargs):
        statsd.incr('main.uploadify_post')
        try:
            if request.FILES:
                # save it locally
                vuuid = uuid.uuid4()
                safe_makedirs(settings.TMP_DIR)
                extension = request.FILES['Filedata'].name.split(".")[-1]
                tmpfilename = settings.TMP_DIR + "/" + str(vuuid) + "."\
                    + extension.lower()
                tmpfile = open(tmpfilename, 'wb')
                for chunk in request.FILES['Filedata'].chunks():
                    tmpfile.write(chunk)
                tmpfile.close()
                return HttpResponse(tmpfilename)
            else:
                statsd.incr('main.uploadify_post_no_file')
        except IOError:
            # this happens when the client connection is lost
            # during the upload. eg, bad wifi, or the user
            # is impatient and hits reload or back, or if they
            # cancel the upload. Not really our fault and not much
            # we can do about it.
            return HttpResponse('False')
        return HttpResponse('True')

    def get(self, request, *args, **kwargs):
        return HttpResponse('True')


class RecentOperationsView(StaffMixin, View):
    def get(self, request):
        submitted = request.GET.get('submitted', '') == '1'
        status_filters = []
        for (status, get_param) in [
            ("failed", "status_filter_failed"),
            ("enqueued", "status_filter_enqueued"),
            ("complete", 'status_filter_complete'),
            ("in progress", 'status_filter_inprogress'),
            ("submitted", 'status_filter_submitted'),
        ]:
            if request.GET.get(get_param, not submitted):
                status_filters.append(status)

        user_filter = request.GET.get('user', '')
        collection_filter = int(request.GET.get('collection', False) or '0')

        q = Operation.objects.filter(status__in=status_filters)
        if collection_filter:
            q = q.filter(video__collection__id=collection_filter)
        if user_filter:
            q = q.filter(video__creator=user_filter)

        return HttpResponse(
            dumps(dict(operations=[o.as_dict() for o
                                   in q.order_by("-modified")[:200]])),
            mimetype="application/json")


@login_required
@user_passes_test(is_staff)
def most_recent_operation(request):
    qs = Operation.objects.all().order_by("-modified")
    if qs.count():
        return HttpResponse(
            dumps(
                dict(
                    modified=str(qs[0].modified)[:19])),
            mimetype="application/json")
    else:
        return HttpResponse(
            dumps(dict()),
            mimetype="application/json")


class SlowOperationsView(StaffMixin, TemplateView):
    template_name = 'main/slow_operations.html'

    def get_context_data(self, *args, **kwargs):
        status_filters = ["enqueued", "in progress", "submitted"]
        operations = Operation.objects.filter(
            status__in=status_filters,
            modified__lt=datetime.now() - timedelta(hours=1)
        ).order_by("-modified")
        return dict(operations=operations)


class ServersListView(StaffMixin, ListView):
    template_name = 'main/servers.html'
    model = Server
    context_object_name = "servers"


class ServerView(StaffMixin, DetailView):
    template_name = 'main/server.html'
    model = Server
    context_object_name = "server"


class DeleteServerView(StaffMixin, DeleteView):
    template_name = 'main/delete_confirm.html'
    model = Server
    success_url = "/server/"


@login_required
@user_passes_test(is_staff)
@render_to('main/edit_server.html')
def edit_server(request, id):
    server = get_object_or_404(Server, id=id)
    if request.method == "POST":
        form = server.edit_form(request.POST)
        if form.is_valid():
            server = form.save()
            return HttpResponseRedirect(server.get_absolute_url())
    form = server.edit_form()
    return dict(server=server, form=form)


@login_required
@user_passes_test(is_staff)
@render_to('main/add_server.html')
def add_server(request):
    if request.method == "POST":
        form = AddServerForm(request.POST)
        if form.is_valid():
            suuid = uuid.uuid4()
            s = form.save(commit=False)
            s.uuid = suuid
            s.save()
            form.save_m2m()
            return HttpResponseRedirect(s.get_absolute_url())
    return dict(form=AddServerForm())


@login_required
@user_passes_test(is_staff)
@render_to('main/collection.html')
def collection(request, id):
    collection = get_object_or_404(Collection, id=id)
    videos = Video.objects.filter(collection=collection).order_by("-modified")
    return dict(
        collection=collection, videos=videos[:20],
        operations=Operation.objects.filter(
            video__collection__id=id).order_by("-modified")[:20])


@login_required
@user_passes_test(is_staff)
@render_to('main/all_collection_videos.html')
def all_collection_videos(request, id):
    collection = get_object_or_404(Collection, id=id)
    videos = collection.video_set.all().order_by("title")
    params = dict(collection=collection)
    paginator = Paginator(videos, 100)

    try:
        page = int(request.GET.get('page', '1'))
    except ValueError:
        page = 1
    try:
        videos = paginator.page(page)
    except (EmptyPage, InvalidPage):
        videos = paginator.page(paginator.num_pages)

    for k, v in request.GET.items():
        params[k] = v
    params.update(dict(videos=videos))
    return params


@login_required
@user_passes_test(is_staff)
@render_to('main/all_collection_operations.html')
def all_collection_operations(request, id):
    collection = get_object_or_404(Collection, id=id)
    operations = Operation.objects.filter(
        video__collection__id=id).order_by("-modified")
    params = dict(collection=collection)
    paginator = Paginator(operations, 100)

    try:
        page = int(request.GET.get('page', '1'))
    except ValueError:
        page = 1
    try:
        operations = paginator.page(page)
    except (EmptyPage, InvalidPage):
        operations = paginator.page(paginator.num_pages)

    for k, v in request.GET.items():
        params[k] = v
    params.update(dict(operations=operations))
    return params


@login_required
@user_passes_test(is_staff)
@render_to('main/user.html')
def user(request, username):
    user = get_object_or_404(User, username=username)
    return dict(
        viewuser=user,
        operations=Operation.objects.filter(
            owner__id=user.id).order_by("-modified")[:20])


@login_required
@user_passes_test(is_staff)
@render_to('main/edit_collection.html')
def edit_collection(request, id):
    collection = get_object_or_404(Collection, id=id)
    if request.method == "POST":
        form = collection.edit_form(request.POST)
        if form.is_valid():
            collection = form.save()
            return HttpResponseRedirect(collection.get_absolute_url())
    form = collection.edit_form()
    return dict(collection=collection, form=form)


@login_required
@user_passes_test(is_staff)
def collection_toggle_active(request, id):
    collection = get_object_or_404(Collection, id=id)
    if request.method == "POST":
        collection.active = not collection.active
        collection.save()
    return HttpResponseRedirect(collection.get_absolute_url())


@login_required
@user_passes_test(is_staff)
@render_to('main/edit_collection_workflows.html')
def edit_collection_workflows(request, id):
    collection = get_object_or_404(Collection, id=id)
    workflows, pcp_error = get_pcp_workflows()

    if request.method == 'POST':
        # clear existing ones
        collection.collectionworkflow_set.all().delete()
        # re-add
        for k in request.POST.keys():
            if k.startswith('workflow_'):
                uuid = k.split('_')[1]
                label = 'default workflow'
                for w in workflows:
                    if w.uuid == uuid:
                        label = w.title
                        break
                cw = CollectionWorkflow.objects.create(
                    collection=collection,
                    workflow=uuid,
                    label=label,
                )
        return HttpResponseRedirect(collection.get_absolute_url())

    existing_uuids = [str(cw.workflow) for cw in
                      collection.collectionworkflow_set.all()]
    for w in workflows:
        if str(w.uuid) in existing_uuids:
            w.selected = True

    return dict(collection=collection, workflows=workflows,
                pcp_error=pcp_error)


@login_required
@user_passes_test(is_staff)
@render_to('main/edit_video.html')
def edit_video(request, id):
    video = get_object_or_404(Video, id=id)
    if request.method == "POST":
        form = video.edit_form(request.POST)
        if form.is_valid():
            video = form.save()
            return HttpResponseRedirect(video.get_absolute_url())
    form = video.edit_form()
    return dict(video=video, form=form)


@login_required
@user_passes_test(is_staff)
def remove_tag_from_video(request, id, tagname):
    video = get_object_or_404(Video, id=id)
    if 'ajax' in request.GET:
        # we're not being strict about requiring POST,
        # but let's at least require ajax
        video.tags.remove(tagname)
    return HttpResponse("ok")


@login_required
@user_passes_test(is_staff)
def remove_tag_from_collection(request, id, tagname):
    collection = get_object_or_404(Collection, id=id)
    if 'ajax' in request.GET:
        # we're not being strict about requiring POST,
        # but let's at least require ajax
        collection.tags.remove(tagname)
    return HttpResponse("ok")


@login_required
@user_passes_test(is_staff)
@render_to('main/tag.html')
def tag(request, tagname):
    return dict(
        tag=tagname,
        collection=Collection.objects.filter(
            tags__name__in=[tagname]).order_by("-modified"),
        videos=Video.objects.filter(
            tags__name__in=[tagname]).order_by("-modified"))


class TagsListView(StaffMixin, ListView):
    template_name = 'main/tags.html'
    queryset = Tag.objects.all().order_by("name")
    context_object_name = "tags"


@login_required
@user_passes_test(is_staff)
@render_to('main/video_index.html')
def video_index(request):
    videos = Video.objects.all()
    creators = request.GET.getlist('creator')
    if len(creators) > 0:
        videos = videos.filter(creator__in=creators)
    descriptions = request.GET.getlist('description')
    if len(descriptions) > 0:
        videos = videos.filter(description__in=descriptions)
    languages = request.GET.getlist('language')
    if len(languages) > 0:
        videos = videos.filter(language__in=languages)
    subjects = request.GET.getlist('subject')
    if len(subjects) > 0:
        videos = videos.filter(subject__in=subjects)
    licenses = request.GET.getlist('license')
    if len(licenses) > 0:
        videos = videos.filter(license__in=licenses)
    paginator = Paginator(videos.order_by('title'), 100)

    try:
        page = int(request.GET.get('page', '1'))
    except ValueError:
        page = 1

    try:
        videos = paginator.page(page)
    except (EmptyPage, InvalidPage):
        videos = paginator.page(paginator.num_pages)
    params = dict()
    for k, v in request.GET.items():
        params[k] = v
    params.update(dict(videos=videos))
    return params


@login_required
@user_passes_test(is_staff)
@render_to('main/file_index.html')
def file_index(request):
    files = File.objects.all()
    params = dict()
    facets = []
    for k, v in request.GET.items():
        params[k] = v
        metadatas = Metadata.objects.filter(field=k, value=v)
        files = files.filter(id__in=[m.file_id for m in metadatas])
        facets.append(dict(field=k, value=v))
    paginator = Paginator(files.order_by('video__title'), 100)

    try:
        page = int(request.GET.get('page', '1'))
    except ValueError:
        page = 1

    try:
        files = paginator.page(page)
    except (EmptyPage, InvalidPage):
        files = paginator.page(paginator.num_pages)
    params.update(dict(files=files, facets=facets))
    return params


@login_required
@user_passes_test(is_staff)
@render_to('main/add_collection.html')
def add_collection(request):
    if request.method == "POST":
        form = AddCollectionForm(request.POST)
        if form.is_valid():
            suuid = uuid.uuid4()
            s = form.save(commit=False)
            s.uuid = suuid
            s.save()
            form.save_m2m()
            return HttpResponseRedirect(s.get_absolute_url())
    return dict(form=AddCollectionForm())


def operation_info(request, uuid):
    operation = get_object_or_404(Operation, uuid=uuid)
    return HttpResponse(dumps(operation.as_dict()),
                        mimetype="application/json")


@login_required
@user_passes_test(is_staff)
@render_to('main/operation.html')
def operation(request, uuid):
    operation = get_object_or_404(Operation, uuid=uuid)
    return dict(operation=operation)


def safe_makedirs(d):
    try:
        os.makedirs(d)
    except:
        pass


def save_file_locally(request):
    vuuid = uuid.uuid4()
    source_filename = None
    tmp_filename = ''
    tmpfilename = ''
    if request.POST.get('scan_directory', False):
        source_filename = request.POST.get('source_file', '')
        statsd.incr('main.upload.scan_directory')
    if request.POST.get('tmpfilename', False):
        tmp_filename = request.POST.get('tmpfilename', '')
    if source_filename:
        safe_makedirs(settings.TMP_DIR)
        extension = source_filename.split(".")[-1]
        tmpfilename = settings.TMP_DIR + "/" + str(vuuid) + "."\
            + extension.lower()
        if request.POST.get('scan_directory', False):
            os.rename(settings.WATCH_DIRECTORY
                      + request.POST.get('source_file'),
                      tmpfilename)
        else:
            tmpfile = open(tmpfilename, 'wb')
            for chunk in request.FILES['source_file'].chunks():
                tmpfile.write(chunk)
            tmpfile.close()
    if tmp_filename.startswith(settings.TMP_DIR):
        tmpfilename = tmp_filename
        filename = os.path.basename(tmpfilename)
        vuuid = os.path.splitext(filename)[0]
        source_filename = tmp_filename

    return (source_filename, tmpfilename, vuuid)


def create_operations(request, v, tmpfilename, source_file, filename):
    operations, params = v.make_default_operations(
        tmpfilename, source_file, request.user)

    if request.POST.get("submit_to_youtube", False):
        o, p = v.make_upload_to_youtube_operation(
            tmpfilename, request.user)
        operations.append(o)
        params.append(p)
    # run collection's default workflow(s)
    for cw in v.collection.collectionworkflow_set.all():
        o, p = v.make_submit_to_podcast_producer_operation(
            tmpfilename, cw.workflow, request.user)
        operations.append(o)
        params.append(p)
    return operations, params


@transaction.commit_manually
@login_required
@user_passes_test(is_staff)
def upload(request):
    if request.method != "POST":
        transaction.rollback()
        return HttpResponseRedirect("/upload/")

    form = UploadVideoForm(request.POST, request.FILES)
    if not form.is_valid():
        # TODO: give the user proper feedback here
        transaction.rollback()
        return HttpResponseRedirect("/upload/")

    collection_id = None
    operations = []
    params = []
    statsd.incr('main.upload')

    # save it locally
    (source_filename, tmpfilename, vuuid) = save_file_locally(request)
    # important to note here that we allow an "upload" with no file
    # so the user can create a placeholder for a later upload,
    # or to associate existing files/urls with

    # make db entry
    try:
        v = form.save(commit=False)
        v.uuid = vuuid
        v.creator = request.user.username
        collection_id = request.GET.get('collection', None)
        if collection_id:
            v.collection_id = collection_id
        v.save()
        form.save_m2m()
        source_file = v.make_source_file(source_filename)

        if source_filename:
            operations, params = create_operations(
                request, v, tmpfilename, source_file, source_filename)
    except:
        statsd.incr('main.upload.failure')
        transaction.rollback()
        raise
    else:
        transaction.commit()
        for o, p in zip(operations, params):
            tasks.process_operation.delay(o.id, p)
    return HttpResponseRedirect("/")


@login_required
@user_passes_test(is_staff)
def rerun_operation(request, operation_id):
    operation = get_object_or_404(Operation, id=operation_id)
    if request.method == "POST":
        operation.status = "enqueued"
        operation.save()
        tasks.process_operation.delay(operation_id, loads(operation.params))
    redirect_to = request.META.get(
        'HTTP_REFERER',
        operation.video.get_absolute_url())
    return HttpResponseRedirect(redirect_to)


@render_to('main/upload.html')
@login_required
@user_passes_test(is_staff)
def upload_form(request):
    form = UploadVideoForm()
    form.fields["collection"].queryset = Collection.objects.filter(active=True)
    collection_id = request.GET.get('collection', None)
    if collection_id:
        collection = get_object_or_404(Collection, id=collection_id)
        form = collection.add_video_form()
    return dict(form=form, collection_id=collection_id)


@login_required
@user_passes_test(is_staff)
@render_to('main/upload.html')
def scan_directory(request):
    collection_id = None
    file_listing = []
    form = UploadVideoForm()
    collection_id = request.GET.get('collection', None)
    if collection_id:
        collection = get_object_or_404(Collection, id=collection_id)
        form = collection.add_video_form()
    file_listing = os.listdir(settings.WATCH_DIRECTORY)
    return dict(form=form, collection_id=collection_id,
                file_listing=file_listing, scan_directory=True)


def test_upload(request):
    return HttpResponse("a response")


def handle_mediathread_submit(operation):
    params = dict()
    if operation.video.is_mediathread_submit():
        statsd.incr('main.upload.mediathread')
        (set_course, username,
         audio, audio2) = operation.video.mediathread_submit()
        if set_course is not None:
            user = User.objects.get(username=username)
            params['set_course'] = set_course
            params['audio'] = audio
            params['audio2'] = audio2
            o = Operation.objects.create(
                uuid=uuid.uuid4(),
                video=operation.video,
                action="submit to mediathread",
                status="enqueued",
                params=dumps(params),
                owner=user
            )
            o.video.clear_mediathread_submit()
            return ([o.id, ], params)
    return ([], dict())


def make_cunix_file(operation, cunix_path):
    if cunix_path.startswith(settings.CUNIX_SECURE_DIRECTORY):
        File.objects.create(video=operation.video,
                            label="CUIT File",
                            filename=cunix_path,
                            location_type='cuit',
                            )
    if cunix_path.startswith(settings.CUNIX_H264_DIRECTORY):
        File.objects.create(video=operation.video,
                            label="CUIT H264",
                            filename=cunix_path,
                            location_type='cuit',
                            )


@transaction.commit_manually
def done(request):
    if 'title' not in request.POST:
        transaction.commit()
        return HttpResponse("expecting a title")
    title = request.POST.get('title', 'no title')
    ouuid = uuidparse(title)
    r = Operation.objects.filter(uuid=ouuid)
    if r.count() != 1:
        transaction.commit()
        return HttpResponse("could not find an operation with that UUID")

    statsd.incr('main.done')
    operations = []
    params = dict()
    try:
        operation = r[0]
        operation.status = "complete"
        operation.save()
        operation.log(info="PCP completed")
        cunix_path = request.POST.get('movie_destination_path', '')
        make_cunix_file(operation, cunix_path)
        (operations, params) = handle_mediathread_submit(operation)
    except:
        statsd.incr('main.upload.failure')
        transaction.rollback()
        raise
    finally:
        transaction.commit()
        for o in operations:
            tasks.process_operation.delay(o, params)

    return HttpResponse("ok")


def posterdone(request):
    if 'title' not in request.POST:
        return HttpResponse("expecting a title")
    title = request.POST.get('title', 'no title')
    uuid = uuidparse(title)
    r = Operation.objects.filter(uuid=uuid)
    if r.count() == 1:
        statsd.incr('main.posterdone')
        operation = r[0]
        cunix_path = request.POST.get('image_destination_path', '')
        poster_url = cunix_path.replace(
            settings.CUNIX_BROADCAST_DIRECTORY,
            settings.CUNIX_BROADCAST_URL)

        File.objects.create(video=operation.video,
                            label="CUIT thumbnail image",
                            url=poster_url,
                            location_type='cuitthumb')
    return HttpResponse("ok")


class VideoView(StaffMixin, DetailView):
    template_name = 'main/video.html'
    model = Video
    context_object_name = "video"


@login_required
@user_passes_test(is_staff)
@render_to('main/file.html')
def file(request, id):
    f = get_object_or_404(File, id=id)
    filename = f.filename
    if filename and filename.startswith(settings.CUNIX_BROADCAST_DIRECTORY):
        filename = filename[len(settings.CUNIX_BROADCAST_DIRECTORY):]
    if f.is_h264_secure_streamable():
        filename = f.h264_secure_path()

    return dict(file=f, filename=filename,
                poster_options=f.poster_options(POSTER_BASE),
                protection_options=f.protection_options(),
                authtype_options=f.authtype_options(),
                )


@login_required
@user_passes_test(is_staff)
@render_to("main/file_surelink.html")
def file_surelink(request, id):
    f = get_object_or_404(File, id=id)
    PROTECTION_KEY = settings.SURELINK_PROTECTION_KEY
    filename = f.filename
    if filename.startswith(settings.CUNIX_BROADCAST_DIRECTORY):
        filename = filename[len(settings.CUNIX_BROADCAST_DIRECTORY):]
    if f.is_h264_secure_streamable():
        filename = f.h264_secure_path()
    if (request.GET.get('protection', '') == 'mp4_public_stream'
            and f.is_h264_public_streamable()):
        filename = f.h264_public_path()
    s = SureLink(filename,
                 int(request.GET.get('width', '0')),
                 int(request.GET.get('height', '0')),
                 request.GET.get('captions', ''),
                 request.GET.get('poster', ''),
                 request.GET.get('protection', ''),
                 request.GET.get('authtype', ''),
                 PROTECTION_KEY)

    return dict(
        surelink=s,
        protection=request.GET.get('protection', ''),
        public=request.GET.get('protection', '').startswith('public'),
        public_mp4_download=request.GET.get(
            'protection',
            '') == "public-mp4-download",
        width=request.GET.get('width', ''),
        height=request.GET.get('height', ''),
        captions=request.GET.get('captions', ''),
        filename=filename,
        file=f,
        poster=request.GET.get('poster', ''),
        poster_options=POSTER_OPTIONS,
        protection_options=f.protection_options(),
        authtype_options=f.authtype_options(),
        authtype=request.GET.get('authtype', ''),
    )


@login_required
@user_passes_test(is_staff)
@render_to('main/delete_confirm.html')
def delete_file(request, id):
    f = get_object_or_404(File, id=id)
    if request.method == "POST":
        video = f.video
        f.delete()
        return HttpResponseRedirect(video.get_absolute_url())
    else:
        return dict()


@login_required
@user_passes_test(is_staff)
@render_to('main/delete_confirm.html')
def delete_video(request, id):
    v = get_object_or_404(Video, id=id)
    if request.method == "POST":
        collection = v.collection
        v.delete()
        return HttpResponseRedirect(collection.get_absolute_url())
    else:
        return dict()


class DeleteCollectionView(StaffMixin, DeleteView):
    template_name = 'main/delete_confirm.html'
    model = Collection
    success_url = "/"


@login_required
@user_passes_test(is_staff)
@render_to('main/delete_confirm.html')
def delete_operation(request, id):
    o = get_object_or_404(Operation, id=id)
    if request.method == "POST":
        video = o.video
        o.delete()
        redirect_to = request.META.get(
            'HTTP_REFERER',
            video.get_absolute_url())
        return HttpResponseRedirect(redirect_to)
    else:
        return dict()


@login_required
@user_passes_test(is_staff)
@render_to('main/pcp_submit.html')
def video_pcp_submit(request, id):
    video = get_object_or_404(Video, id=id)
    o = None
    p = None
    if request.method == "POST":
        statsd.incr('main.video_pcp_submit')
        # send to podcast producer
        o, p = video.make_pull_from_tahoe_and_submit_to_pcp_operation(
            video.id, request.POST.get('workflow', ''), request.user)
        # TODO: manual transaction processing here
        tasks.process_operation.delay(o.id, p)
        return HttpResponseRedirect(video.get_absolute_url())
    workflows, pcp_error = get_pcp_workflows()
    return dict(video=video, workflows=workflows, pcp_error=pcp_error,
                kino_base=settings.PCP_BASE_URL)


@login_required
@user_passes_test(is_staff)
@render_to('main/file_pcp_submit.html')
def file_pcp_submit(request, id):
    file = get_object_or_404(File, id=id)
    if request.method == "POST":
        statsd.incr('main.file_pcp_submit')
        video = file.video
        # send to podcast producer
        o, p = video.make_pull_from_cuit_and_submit_to_pcp_operation(
            video.id, request.POST.get('workflow', ''), request.user)
        # TODO: manual transaction processing here
        tasks.process_operation.delay(o.id, p)
        return HttpResponseRedirect(video.get_absolute_url())
    workflows, pcp_error = get_pcp_workflows()
    return dict(file=file, workflows=workflows, pcp_error=pcp_error,
                kino_base=settings.PCP_BASE_URL)


@login_required
@user_passes_test(is_staff)
@render_to('main/file_filter.html')
def file_filter(request):

    include_collection = request.GET.getlist('include_collection')
    include_file_types = request.GET.getlist('include_file_types')
    include_video_formats = request.GET.getlist('include_video_formats')
    include_audio_formats = request.GET.getlist('include_audio_formats')

    results = File.objects.filter(
        video__collection__id__in=include_collection
    ).filter(location_type__in=include_file_types)

    all_collection = [(s, str(s.id) in include_collection)
                      for s in Collection.objects.all()]

    all_file_types = [(l, l in include_file_types)
                      for l in list(set([f.location_type
                                         for f in File.objects.all()]))]

    all_video_formats = []
    excluded_video_formats = []
    for vf in [""] + list(
        set(
            [
                m.value for m
                in Metadata.objects.filter(
                    field="ID_VIDEO_FORMAT")])):
        all_video_formats.append((vf, vf in include_video_formats))
        if vf not in include_video_formats:
            excluded_video_formats.append(vf)
            if vf == "":
                excluded_video_formats.append(None)
    all_audio_formats = []
    excluded_audio_formats = []
    for af in [""] + list(
        set(
            [
                m.value for m
                in Metadata.objects.filter(
                    field="ID_AUDIO_FORMAT")])):
        all_audio_formats.append((af, af in include_audio_formats))
        if af not in include_audio_formats:
            excluded_audio_formats.append(af)
            if af == "":
                excluded_audio_formats.append(None)

    files = [f for f in results
             if f.video_format() not in excluded_video_formats
             and f.audio_format() not in excluded_audio_formats]

    return dict(all_collection=all_collection,
                all_video_formats=all_video_formats,
                all_audio_formats=all_audio_formats,
                all_file_types=all_file_types,
                files=files,
                )


@login_required
@user_passes_test(is_staff)
@render_to('main/bulk_file_operation.html')
def bulk_file_operation(request):
    if request.method == "POST":
        files = [File.objects.get(id=int(f.split("_")[1]))
                 for f in request.POST.keys() if f.startswith("file_")]
        for file in files:
            video = file.video
            # send to podcast producer
            tasks.pull_from_cuit_and_submit_to_pcp.delay(
                video.id,
                request.user,
                request.POST.get('workflow',
                                 ''),
                settings.PCP_BASE_URL,
                settings.PCP_USERNAME,
                settings.PCP_PASSWORD)
            statsd.incr('main.bulk_file_operation')
        return HttpResponseRedirect("/")
    files = [File.objects.get(id=int(f.split("_")[1]))
             for f in request.GET.keys() if f.startswith("file_")]
    workflows, pcp_error = get_pcp_workflows()
    return dict(files=files, workflows=workflows, pcp_error=pcp_error,
                kino_base=settings.PCP_BASE_URL)


@login_required
@user_passes_test(is_staff)
@render_to('main/add_file.html')
def video_add_file(request, id):
    video = get_object_or_404(Video, id=id)
    if request.method == "POST":
        form = video.add_file_form(request.POST)
        if form.is_valid():
            f = form.save(commit=False)
            f.video = video
            f.save()
        else:
            pass
        return HttpResponseRedirect(video.get_absolute_url())
    return dict(video=video)


class VideoSelectPosterView(StaffMixin, View):
    def get(self, request, id, image_id):
        video = get_object_or_404(Video, id=id)
        image = get_object_or_404(Image, id=image_id)
        # clear any existing ones for the video
        Poster.objects.filter(video=video).delete()
        Poster.objects.create(video=video, image=image)
        return HttpResponseRedirect(video.get_absolute_url())


class ListWorkflowsView(StaffMixin, TemplateView):
    template_name = 'main/workflows.html'

    def get_context_data(self):
        workflows, error_message = get_pcp_workflows()
        return dict(workflows=workflows,
                    error_message=error_message,
                    kino_base=settings.PCP_BASE_URL)


class SearchView(StaffMixin, TemplateView):
    template_name = "main/search.html"

    def get_context_data(self):
        q = self.request.GET.get('q', '')
        results = dict(count=0)
        if q:
            r = Collection.objects.filter(
                Q(title__icontains=q) |
                Q(creator__icontains=q) |
                Q(contributor__icontains=q) |
                Q(language__icontains=q) |
                Q(description__icontains=q) |
                Q(subject__icontains=q) |
                Q(license__icontains=q)
            )
            results['count'] += r.count()
            results['collection'] = r

            r = Video.objects.filter(
                Q(title__icontains=q) |
                Q(creator__icontains=q) |
                Q(language__icontains=q) |
                Q(description__icontains=q) |
                Q(subject__icontains=q) |
                Q(license__icontains=q)
            )
            results['count'] += r.count()
            results['videos'] = r

        return dict(q=q, results=results)


class UUIDSearchView(StaffMixin, TemplateView):
    template_name = "main/uuid_search.html"

    def get_context_data(self):
        uuid = self.request.GET.get('uuid', '')
        results = dict()
        if uuid:
            for k, label in [
                    (Collection, "collection"),
                    (Video, "video"),
                    (Operation, "operation")]:
                r = k.objects.filter(uuid=uuid)
                if r.count() > 0:
                    results[label] = r[0]
                    break
        return dict(uuid=uuid, results=results)


class TagAutocompleteView(View):
    def get(self, request):
        q = request.GET.get('q', '')
        r = Tag.objects.filter(name__icontains=q)
        return HttpResponse("\n".join([t.name for t in list(r)]))


class SubjectAutocompleteView(View):
    def get(self, request):
        q = request.GET.get('q', '')
        q = q.lower()
        r = Video.objects.filter(subject__icontains=q)
        all_subjects = dict()
        for v in r:
            s = v.subject.lower()
            for p in s.split(","):
                p = p.strip()
                all_subjects[p] = 1
        r = Collection.objects.filter(subject__icontains=q)
        for v in r:
            s = v.subject.lower()
            for p in s.split(","):
                p = p.strip()
                all_subjects[p] = 1

        return HttpResponse("\n".join(all_subjects.keys()))

POSTER_BASE = settings.CUNIX_BROADCAST_URL + "posters/vidthumb"
POSTER_OPTIONS = [
    dict(value="default_custom_poster",
         label="broadcast/posters/[media path]/[filename].jpg"),
    dict(value=POSTER_BASE + "_480x360.jpg",
         label="CCNMTL 480x360"),
    dict(value=POSTER_BASE + "_480x272.jpg",
         label="CCNMTL 480x272"),
    dict(value=POSTER_BASE + "_320x240.jpg",
         label="CCNMTL 320x240"),
]


class SureLinkView(TemplateView):
    template_name = "main/surelink.html"

    def get_context_data(self):
        PROTECTION_KEY = settings.SURELINK_PROTECTION_KEY
        results = []
        if self.request.GET.get('files', ''):
            for filename in self.request.GET.get('files', '').split('\n'):
                filename = filename.strip()
                s = SureLink(filename,
                             int(self.request.GET.get('width', '0')),
                             int(self.request.GET.get('height', '0')),
                             self.request.GET.get('captions', ''),
                             self.request.GET.get('poster', ''),
                             self.request.GET.get('protection', ''),
                             self.request.GET.get('authtype', ''),
                             PROTECTION_KEY)
                results.append(s)
        return dict(
            protection=self.request.GET.get('protection', ''),
            public=self.request.GET.get(
                'protection', '').startswith('public'),
            public_mp4_download=self.request.GET.get(
                'protection', '') == "public-mp4-download",
            width=self.request.GET.get('width', ''),
            height=self.request.GET.get('height', ''),
            captions=self.request.GET.get('captions', ''),
            results=results,
            rows=len(results) * 3,
            files=self.request.GET.get('files', ''),
            poster=self.request.GET.get('poster', ''),
            poster_options=POSTER_OPTIONS,
            protection_options=PROTECTION_OPTIONS,
            authtype_options=AUTHTYPE_OPTIONS,
            authtype=self.request.GET.get('authtype', ''),
        )
