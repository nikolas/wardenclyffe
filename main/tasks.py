import urllib2
from poster.encode import multipart_encode, MultipartParam
from poster.streaminghttp import register_openers
from angeldust import PCP
from celery.decorators import task
from wardenclyffe.main.models import Video, File, Operation, OperationFile, OperationLog, Image, Poster
import os.path
import uuid 
import tempfile
import subprocess
from django.conf import settings
from restclient import POST
import httplib
from django.core.mail import send_mail
import paramiko
import random

# TODO: convert to decorator
def with_operation(f,video,action,params,user,args,kwargs):
    ouuid = uuid.uuid4()
    operation = Operation.objects.create(video=video,
                                         action=action,
                                         status="in progress",
                                         params=params,
                                         owner=user,
                                         uuid=ouuid)
    try:
        (success,message) = f(video,user,operation,*args,**kwargs)
        operation.status = success
        if operation.status == "failed" or message != "":
            log = OperationLog.objects.create(operation=operation,
                                              info=message)
    except Exception, e:
        operation.status = "failed"
        log = OperationLog.objects.create(operation=operation,
                                          info=str(e))
    operation.save()

def save_file_to_tahoe(operation,params):
    source_file = open(params['tmpfilename'],"rb")
    register_openers()
    datagen, headers = multipart_encode((
            ("t","upload"),
            MultipartParam(name='file',fileobj=source_file,
                           filename=os.path.basename(params['tmpfilename']))))
    request = urllib2.Request(params['tahoe_base'], datagen, headers)
    cap = urllib2.urlopen(request).read()
    source_file.close()
    f = File.objects.create(video=operation.video,url="",cap=cap,
                            location_type="tahoe",
                            filename=params['filename'],
                            label="uploaded source file")
    of = OperationFile.objects.create(operation=operation,file=f)
    return ("complete","")


@task(ignore_result=True)
def submit_to_mediathread(video_id,user,course_id,mediathread_secret,mediathread_base,**kwargs):
    print "submitting to mediathread"
    video = Video.objects.get(id=video_id)

    action = "submit to mediathread"
    def _do_submit_to_mediathread(video,user,operation,course_id,mediathread_secret,mediathread_base,**kwargs):
        (width,height) = video.get_dimensions()
        if not width or not height:
            return ("failed","could not figure out dimensions")
        if not video.tahoe_download_url():
            return ("failed","no video URL")
        params = {
            'set_course' : course_id,
            'as' : user.username,
            'secret' : mediathread_secret,
            'title' : video.title,
            'thumb' : video.cuit_poster_url() or video.poster_url(),
            "metadata-creator" : video.creator,
            "metadata-description" : video.description,
            "metadata-subject" : video.subject,
            "metadata-license" : video.license,
            "metadata-language" : video.language,
            "metadata-uuid" : video.uuid,
            "metadata-wardenclyffe-id" : str(video.id),
            }
        if video.cuit_url():
            params['flv_pseudo'] = video.cuit_url()
            params['flv_pseudo-metadata'] = "w%dh%d" % (width,height)
        else:
            params['mp4'] = video.tahoe_download_url()
            params["mp4-metadata"] = "w%dh%d" % (width,height)

        resp,content = POST(mediathread_base + "/save/",
                            params=params,async=False,resp=True)
        if resp.status == 302:
            url = resp['location']
            f = File.objects.create(video=video,url=url,cap="",
                                    location_type="mediathread",
                                    filename="",
                                    label="mediathread")
            of = OperationFile.objects.create(operation=operation,file=f)
            return ("complete","")
        else:
            return ("failed","mediathread rejected submission")
    args = [course_id,mediathread_secret,mediathread_base]
    with_operation(_do_submit_to_mediathread,video,
                   "submit to mediathread","",
                   user,args,kwargs)
    print "done submitting to mediathread"

@task(ignore_result=True)
def submit_to_vital(video_id,user,course_id,rtsp_url,vital_secret,vital_base,**kwargs):
    print "submitting to vital"
    video = Video.objects.get(id=video_id)

    action = "submit to vital"
    def _do_submit_to_vital(video,user,operation,course_id,rtsp_url,vital_secret,vital_base,**kwargs):
        (width,height) = video.get_dimensions()
        if not width or not height:
            return ("failed","could not figure out dimensions")
        if not rtsp_url:
            return ("failed","no video URL")
        params = {
            'set_course' : course_id,
            'as' : user.username,
            'secret' : vital_secret,
            'title' : video.title,
            'url' : rtsp_url,
            'thumb' : video.vital_thumb_url(),
            }
        resp,content = POST(vital_base,params=params,async=False,resp=True)
        if resp.status == 302 or resp.status == 200:
            send_mail('VITAL video uploaded', 
                  """Your video, "%s", has been uploaded to VITAL.""" % video.title, 
                  'wardenclyffe@wardenclyffe.ccnmtl.columbia.edu',
                  ["%s@columbia.edu" % user.username], fail_silently=False)
            # annoy the video team
            for vuser in settings.ANNOY_EMAILS:
                send_mail('VITAL video uploaded', 
                          """Your video, "%s", has been uploaded to VITAL.""" % video.title, 
                          'wardenclyffe@wardenclyffe.ccnmtl.columbia.edu',
                          [vuser], fail_silently=False)
            return ("complete","")
        else:
            send_mail('VITAL video upload failed', 
                  """An error has occurred while attempting to upload your video, "%s", to VITAL.
Please contact CCNMTL video staff for assistance. 
The error encountered:
%s
""" % (video.title,content), 
                  'wardenclyffe@wardenclyffe.ccnmtl.columbia.edu',
                  ["%s@columbia.edu" % user.username], fail_silently=False)
            for vuser in settings.ANNOY_EMAILS:
                send_mail('VITAL video upload failed', 
                          """An error has occurred while attempting to upload your video, "%s", to VITAL.
Please contact CCNMTL video staff for assistance. 
The error encountered:
%s
""" % (video.title,content), 
                          'wardenclyffe@wardenclyffe.ccnmtl.columbia.edu',
                          [vuser], fail_silently=False)

            return ("failed","vital rejected submission: %s" % content)
    args = [course_id,rtsp_url,vital_secret,vital_base]
    with_operation(_do_submit_to_vital,video,
                   "submit to vital","",
                   user,args,kwargs)
    print "done submitting to vital"

def make_images(operation,params):
    ouuid = operation.uuid
    tmpfilename = params['tmpfilename']
    tmpdir = settings.TMP_DIR + "/imgs/" + str(ouuid) + "/"
    try:
        os.makedirs(tmpdir)
    except:
        pass
    size = os.stat(tmpfilename)[6] / (1024 * 1024)
    frames = size * 2 # 2 frames per MB at the most
    if tmpfilename.lower().endswith("avi"):
        command = "/usr/bin/ionice -c 3 /usr/bin/mplayer -nosound -vo jpeg:outdir=%s -endpos 03:00:00 -frames %d -sstep 10 -correct-pts '%s' 2>/dev/null" % (tmpdir,frames,tmpfilename)
    else:
        command = "/usr/bin/ionice -c 3 /usr/bin/mplayer -nosound -vo jpeg:outdir=%s -endpos 03:00:00 -frames %d -sstep 10 '%s' 2>/dev/null" % (tmpdir,frames,tmpfilename)
    os.system(command)
    imgs = os.listdir(tmpdir)
    if len(imgs) == 0:
        command = "/usr/bin/ionice -c 3 /usr/bin/mplayer -nosound -vo jpeg:outdir=%s -endpos 03:00:00 -frames %d -vf framerate=250 '%s' 2>/dev/null" % (tmpdir,frames,tmpfilename)
        os.system(command)
    # TODO: parameterize
    imgdir = "/var/www/wardenclyffe/uploads/images/%05d/" % operation.video.id
    try:
        os.makedirs(imgdir)
    except:
        pass
    imgs = os.listdir(tmpdir)
    imgs.sort()
    for img in imgs:
        os.system("mv %s%s %s" % (tmpdir,img,imgdir))
        i = Image.objects.create(video=operation.video,image="images/%05d/%s" % (operation.video.id,img))

    if Poster.objects.filter(video=operation.video).count() == 0 and len(imgs) > 0:
        # pick a random image out of the set and assign it as the poster on the video
        r = random.randint(0,len(imgs) - 1)
        image = Image.objects.filter(video=operation.video)[r]
        p = Poster.objects.create(video=operation.video,image=image)

    return ("complete","created %d images" % len(imgs))

def extract_metadata(operation,params):
    source_file = File.objects.get(id=params['source_file_id'])
    # warning: for now we're expecting the midentify script
    # to be relatively located to this file. this ought to 
    # be a bit more configurable
    pwd = os.path.dirname(__file__)
    script_dir = os.path.join(pwd,"../scripts/")
    output = subprocess.Popen([os.path.join(script_dir,"midentify.sh"), params['tmpfilename']], stdout=subprocess.PIPE).communicate()[0]
    pairs = [l.strip().split("=") for l in output.split("\n")]
    for line in output.split("\n"):
        try:
            line = line.strip()
            if "=" not in line:
                continue
            (f,v) = line.split("=")
            source_file.set_metadata(f,v)
        except Exception, e:
            # just ignore any parsing issues
            print "exception in extract_metadata: " + str(e)
            print line
    return ("complete","")

@task(ignore_results=True)
def process_operation(operation_id,params,**kwargs):
    print "process_operation(%s,%s)" % (operation_id,str(params))
    operation = Operation.objects.get(id=operation_id)
    operation.process(params)

@task(ignore_result=True)
def submit_to_podcast_producer(tmpfilename,video_id,user,workflow,pcp_base_url,pcp_username,pcp_password,**kwargs):
    print "submitting to PCP"
    video = Video.objects.get(id=video_id)
    def _do_submit_to_podcast_producer(video,user,operation,tmpfilename,workflow,pcp_base_url,pcp_username,pcp_password,**kwargs):
        ouuid = operation.uuid
        pcp = PCP(pcp_base_url,pcp_username,pcp_password)
        # TODO: probably don't always want it to be .mp4
        filename = str(ouuid) + ".mp4"
        fileobj = open(tmpfilename)
        title = "%s-%s" % (str(ouuid),video.title)
        title = title.replace(" ","_") # podcast producer not so good with spaces
        print "submitted with title %s" % title
        pcp.upload_file(fileobj,filename,workflow,title,video.description)
        return ("submitted","")
    with_operation(_do_submit_to_podcast_producer,video,"submit to podcast producer",
                   "workflow: %s" % workflow,user,
                   [tmpfilename,workflow,pcp_base_url,pcp_username,pcp_password],
                   kwargs)
    

@task(ignore_result=True)
def pull_from_tahoe_and_submit_to_pcp(video_id,user,workflow,pcp_base_url,pcp_username,pcp_password,**kwargs):
    print "pulling from tahoe"
    video = Video.objects.get(id=video_id)
    args = [workflow,pcp_base_url,pcp_username,pcp_password]
    def _do_pull_from_tahoe_and_submit_to_pcp(video,user,operation,workflow,pcp_base_url,pcp_username,pcp_password,**kwargs):
        ouuid = operation.uuid
        url = video.tahoe_download_url()
        if url == "":
            return ("failed","does not have a tahoe stored file")
        if workflow == "":
            return ("failed","no workflow specified")
        filename = video.filename()
        suffix = os.path.splitext(filename)[1]
        t = tempfile.NamedTemporaryFile(suffix=suffix)
        r = urllib2.urlopen(url)
        t.write(r.read())
        t.seek(0)
        log = OperationLog.objects.create(operation=operation,
                                          info="downloaded from tahoe")
        # TODO: figure out how to re-use submit_to_podcast_producer()
        print "submitting to PCP"
        pcp = PCP(pcp_base_url,pcp_username,pcp_password)
        filename = str(ouuid) + suffix
        print "submitted with filename %s" % filename
        title = "%s-%s" % (str(ouuid),video.title)
        title = title.replace(" ","_") # podcast producer not so good with spaces
        print "submitted with title %s" % title
        pcp.upload_file(t,filename,workflow, title, video.description)
        return ("submitted","submitted to PCP")
    with_operation(_do_pull_from_tahoe_and_submit_to_pcp,video,
                   "pull from tahoe and submit to pcp",
                   "workflow: %s" % workflow,
                   user,args,kwargs)


def sftp_get(remote_filename,local_filename):
    print "sftp_get(%s,%s)" % (remote_filename,local_filename)
    sftp_hostname = settings.SFTP_HOSTNAME 
    sftp_path = settings.SFTP_PATH 
    sftp_user = settings.SFTP_USER 
    sftp_private_key_path = settings.SSH_PRIVATE_KEY_PATH
    mykey = paramiko.RSAKey.from_private_key_file(sftp_private_key_path)
    transport = paramiko.Transport((sftp_hostname, 22))
    transport.connect(username=sftp_user, pkey = mykey)
    sftp = paramiko.SFTPClient.from_transport(transport)

    try:
        sftp.get(remote_filename, local_filename)
    except Exception, e:
        print "sftp fetch failed"
        raise
    else:
        print "sftp_get succeeded"
    finally:
        sftp.close()
        transport.close()


@task(ignore_result=True)
def pull_from_cuit_and_submit_to_pcp(video_id,user,workflow,pcp_base_url,pcp_username,pcp_password,**kwargs):
    print "pulling from tahoe"
    video = Video.objects.get(id=video_id)
    args = [workflow,pcp_base_url,pcp_username,pcp_password]
    def _do_pull_from_cuit_and_submit_to_pcp(video,user,operation,workflow,pcp_base_url,pcp_username,pcp_password,**kwargs):
        if workflow == "":
            return ("failed","no workflow specified")

        ouuid = operation.uuid
        cuit_file = video.file_set.filter(video=video,location_type="cuit")[0]

        filename = cuit_file.filename
        extension = os.path.splitext(filename)[1]
        tmpfilename = os.path.join(settings.TMP_DIR,str(ouuid) + extension)
        sftp_get(filename,tmpfilename)

        log = OperationLog.objects.create(operation=operation,
                                          info="downloaded from cuit")

        print "submitting to PCP"
        pcp = PCP(pcp_base_url,pcp_username,pcp_password)
        filename = str(ouuid) + extension
        print "submitted with filename %s" % filename
        title = "%s-%s" % (str(ouuid),video.title)
        title = title.replace(" ","_") # podcast producer not so good with spaces
        print "submitted with title %s" % title
        pcp.upload_file(open(tmpfilename,"r"),filename,workflow, title, video.description)
        return ("submitted","submitted to PCP")
    with_operation(_do_pull_from_cuit_and_submit_to_pcp,video,
                   "pull from cuit and submit to pcp",
                   "workflow: %s" % workflow,
                   user,args,kwargs)

    
@task(ignore_result=True)
def flv_encode(video_id,user,basedir,infile,outfile,ffmpeg_path):
    print "flv_encode"
    args = [basedir,infile,outfile,ffmpeg_path]
    def _do_flv_encode(video,user,operation,basedir,infile,outfile,ffmpeg_path):
        command = """%s -i "%s/%s" -y -f flv -vcodec flv -qmin 1 -b 800k -s '480x360' -me_method epzs -r 29.97 -g 100 -qcomp 0.6 -qmax 15 -qdiff 4 -i_qfactor 0.71428572 -b_qfactor 0.76923078 -subq 6 -acodec libmp3lame -ab 128k -ar 22050 -ac 2 -benchmark "%s/%s" """ % (ffmpeg_path,basedir,infile,basedir,outfile)
        os.system(command)
        return ("complete","flv encoded")

    with_operation(_flv_encode,video,"flv encode",
                   "workflow: %s" % workflow,user,args,kwargs)
