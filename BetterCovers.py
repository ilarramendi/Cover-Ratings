from glob import glob
from subprocess import call, getstatusoutput
from os import access, W_OK
from os.path import exists, realpath, join
import json
from sys import argv, exit
from datetime import timedelta
import time
from threading import Thread
import functions
from requests import post
from re import findall

config = {}
tasks = []
running = True
processing = True

# region Functions
def generateTasks(metadata, path, type, title, overWrite, season = False, episode = False):
    tasks = []
    conf = config[type]
    tsk = {
        'out': join(path, conf['output']) if type != 'episode' else path.rpartition('.')[0] + '.jpg',
        'type': type,
        'title': title,
        'season': season,
        'episode': episode,
        'overwrite': overWrite,
        'generateImage': path if type == 'episode' and config['episode']['generateImages'] else False}

    if 'mediainfo' in metadata:
        cfg = []
        if 'HDR' in metadata['mediainfo'] and 'UHD' in metadata['mediainfo'] and conf['mediainfo']['config']['UHD-HDR']:
            metadata['mediainfo'].remove('HDR')
            metadata['mediainfo'].remove('UHD')
            cfg.append('UHD-HDR')
        for mi in metadata['mediainfo']:
            if mi in conf['mediainfo']['config'] and conf['mediainfo']['config'][mi]: cfg.append(mi)
        if len(cfg) > 0:
            tsk['mediainfo'] = cfg

    if 'language' in metadata:
        for lg in conf['mediainfo']['audio'].split(','):
            if lg in metadata['language']:
                tsk['language'] = lg
                break

    if 'ratings' in metadata:
        cfg = {}
        for rt in metadata['ratings']:
            if conf['ratings']['config'][rt]: cfg[rt] = metadata['ratings'][rt]
        if len(cfg) > 0:
            tsk['ratings'] = cfg

    if 'certification' in metadata and conf['certifications']['config'][metadata['certification']]: 
        tsk['certification'] = metadata['certification']

    if type == 'backdrop':
        if 'backdrop' in metadata:
            tsk['image'] = metadata['backdrop']
    elif 'cover' in metadata: tsk['image'] = metadata['cover']

    if overWrite or not exists(tsk['out']):
        if ('image' in tsk or tsk['generateImage']) and ('mediainfo' in tsk or 'ratings' in tsk):
            tasks += [tsk]
    else: functions.log('Existing poster image found for: ' + title + (' S' + str(season) if season else '') + ('E' + str(episode) if episode else ''), 3, 3)

    # Generate sesons tasks
    if type == 'tv' and 'seasons' in metadata:
        for sn in metadata['seasons']:
            tasks += generateTasks(metadata['seasons'][sn], metadata['seasons'][sn]['path'], 'season', title, overWrite, sn)

    # Generate episodes tasks
    if type == 'season' and 'episodes' in metadata:
        for ep in metadata['episodes']:
            tasks += generateTasks(metadata['episodes'][ep], metadata['episodes'][ep]['path'], 'episode', title, overWrite, season, ep)

    # Generate backdrop task
    if type in ['movie', 'tv', 'season']: 
        tasks += generateTasks(metadata, path, 'backdrop', title, overWrite, season, episode)
    
    return tasks

def processFolder(folder):
    st = time.time()
    seasons = functions.getSeasons(folder)
    type = 'tv' if len(seasons) > 0 else 'movie'
    
    # Get name and year
    inf = findall("\/([^\/]+)[ \.]\(?(\d{4})\)?", folder)
    if len(inf) == 0: 
        inf = findall("\/([^\/]+)$", folder)
        if len(inf) == 0:
            functions.log('Cant parse name from: ' + folder, 3, 1)
            return
        else:
            name = inf[0]
            year = False
    else:
        name = inf[0][0].translate({'.': ' ', '_': ' '})
        year =  inf[0][1]

    if type == 'movie' and not overWrite and exists(folder + '/' + config['movie']['output']) and exists(folder + '/' + config['backdrop']['output']):
        return functions.log('Existing cover image found for: ' + name, 3, 3)
    
    metadata = functions.getMetadata(name, type, year, config['omdbApi'], config['tmdbApi'])

    if type == 'tv':
        sns = functions.getSeasonsMetadata(
            metadata['imdbid'] if 'imdbid' in metadata else False,
            metadata['tmdbid'] if 'tmdbid' in metadata else False,
            seasons,
            config['omdbApi'],
            config['tmdbApi'],
            functions.getConfigEnabled(config['tv']['mediainfo']['config']) or functions.getConfigEnabled(config['season']['mediainfo']['config']),
            minVotes,
            name,
            not exists(folder + '/' + config['tv']['output']) or not exists(folder + '/' + config['backdrop']['output']),
            overWrite)
        metadata['seasons'] = sns['seasons']   
        if 'mediainfo' in sns: metadata['mediainfo'] = sns['mediainfo'] 
    elif type == 'movie' and functions.getConfigEnabled(config['movie']['mediainfo']['config']):
        mediaFiles = []
        for ex in functions.extensions: 
            mediaFiles += glob(join(folder.translate({91: '[[]', 93: '[]]'}), '*.' + ex))
        
        mediaFiles = [fl for fl in mediaFiles if 'trailer' not in fl]
        if len(mediaFiles) > 0:
            minfo = functions.getMediaInfo(mediaFiles[0])
            if minfo: 
                metadata['mediainfo'] = minfo['metadata']
                metadata['language'] = minfo['language']
            else: functions.log('Error getting mediainfo for: ' + name, 1, 1)
        else: functions.log('Error getting mediainfo no video files found on: ' + folder, 3, 1)

    global tasks
    tasks += generateTasks(metadata, folder, type, name, overWrite)
    functions.log('Metadata and mediainfo found for: ' + name + ' in ' + str(timedelta(seconds=round(time.time() - st))), 2)

def processTask(task, thread):
    st = time.time()
    img = functions.generateImage(
        config[task['type']],
        task['ratings'] if 'ratings' in task else False,
        task['certification'] if 'certification' in task else False,
        task['language'] if 'language' in task else False,
        task['mediainfo'] if 'mediainfo' in task else False,
        task['image'] if not task['generateImage'] else False,
        thread,
        coverHTML,
        task['out'],
        task['generateImage'])
    
    functions.log('[' + thread + '] ' +
        ('Succesfully generated ' if img else 'Error generating ') + ('backdrop' if task['type'] == 'backdrop' else 'cover') +
        ' image for ' +
        task['title'] +
        (' S' + str(task['season']) if task['season'] else '') +
        ('E' + str(task['episode']) if task['episode'] else '') +
        ' in ' +
        str(round(time.time() - st)) + 's',
        2 if img else 1)

def loadConfig(cfg):
    try:
        with open(cfg, 'r') as js:
            global config 
            config = json.load(js)
            if '-omdb' in argv and argv[argv.index('-omdb') + 1] != '': config['omdbApi'] = argv[argv.index('-omdb') + 1]
            if '-tmdb' in argv and argv[argv.index('-omdb') + 1] != '': config['tmdbApi'] = argv[argv.index('-tmdb') + 1]
        with open(cfg, 'w') as out: 
            out.write(json.dumps(config, indent = 5))
    except:
        functions.log('Error loading config file from: ' + cfg, 1, 0)
        exit()

def processFolders(folders):
    thrs = [False] * threads
    for folder in folders:
        i = 0
        while True:
            if not (thrs[i] and thrs[i].is_alive()):
                thread = Thread(target=processFolder , args=(folder, ))
                thread.start()
                thrs[i] = thread
                break
            i += 1
            if i == threads: i = 0
        if not running: break

    # Wait for threads to end
    for th in thrs: 
        if th and running: th.join()

def processTasks():
    j = 1
    thrs = [False] * threads
    thrsLength = len(str(threads))

    if not exists(join(pt, 'threads')): call(['mkdir', join(pt, 'threads')])
    for i in range(threads):
        pth = join(pt, 'threads', str(i).zfill(thrsLength))
        if not exists(pth): call(['mkdir', pth])

    while running and (processing or len(tasks) > 0):
        if len(tasks) > 0:
            i = 0
            while True:
                if not (thrs[i] and thrs[i].is_alive()):
                    thread = Thread(target=processTask, args=(tasks.pop(), str(i).zfill(thrsLength), ))
                    thread.start()
                    thrs[i] = thread
                    j += 1
                    break
                i += 1
                if i == threads: i = 0

    for th in thrs: 
        if th and running: th.join()    
# endregion

# region Params
overWrite = '-o' in argv and argv[argv.index('-o') + 1] == 'true'
threads = 20 if not '-w' in argv else int(argv[argv.index('-w') + 1])
config = {}
pt = argv[1]
cfg = './config.json' if '-c' not in argv else argv[argv.index('-c') + 1]
folders = sorted(glob(pt)) if '*' in pt else [pt]
gstart = time.time()
if not exists(pt) and '*' in pt and len(glob(pt)) == 0:
    functions.log('Media path doesnt exist', 1, 0)
    exit()
# endregion

# region Files
if not exists(cfg):
    functions.log('Missing config.json, downloading default config.', 0, 3)
    if call(['wget', '-O', cfg, 'https://raw.githubusercontent.com/ilarramendi/Cover-Ratings/main/config.json', '-q']) == 0:
        functions.log('Succesfully downloaded default config file', 2, 0)
        loadConfig(cfg)
    else: functions.log('Error downloading default config file', 1, 0)
    exit()
    
loadConfig(cfg)
if config['tmdbApi'] == '' and config['omdbApi'] == '':
    functions.log('A single api key is needed to work', 1, 0)
    exit() 

if exists(functions.resource_path('cover.html')): 
    with open(functions.resource_path('cover.html'), 'r') as fl: coverHTML = fl.read()
else:
    functions.log('Missing cover.html', 1, 0)
    exit()
if not exists(functions.resource_path('cover.css')):
    functions.log('Missing cover.css', 1, 0)
    exit()

try:
    pt = sys._MEIPASS
except Exception: 
    pt = realpath(__file__).rpartition('/')[0]
# endregion

# region Check Dependencies
dependencies = [
    'mediainfo' if functions.getConfigEnabled(config['tv']['mediainfo']['config']) or functions.getConfigEnabled(config['season']['mediainfo']['config']) or functions.getConfigEnabled(config['episode']['mediainfo']['config']) or functions.getConfigEnabled(config['movie']['mediainfo']['config']) else False,
    'cutycapt',
    'ffmpeg' if config['episode']['generateImages'] else False]

for dp in [d for d in dependencies if d]:
    cl = getstatusoutput('apt-cache policy ' + dp)[1]
    if 'Installed: (none)' in cl:
        functions.log(dp + ' is not installed', 1, 0)
        exit()
# endregion


try:
    functions.log('PROCESSING ' + str(len(folders)) + ' FOLDERS')

    # Generate tasks for each folder
    PROCESSING = Thread(target=processFolders , args=(folders, ))
    PROCESSING.start()
    # Process generated tasks
    GENERATING = Thread(target=processTasks, args=())
    GENERATING.start()

    PROCESSING.join()
    processing = False
    functions.log('Finished generating tasks', 0, 2)
    GENERATING.join()
except KeyboardInterrupt:
    functions.log('Closing BetterCovers!', 3, 0)
    running = False
    functions.logLevel = 0
    GENERATING.join()
    PROCESSING.join()
    

call(['rm', '-r', join(pt, 'threads')])
if running: # Update agent library
    if config['agent']['apiKey'] != '':
        url = config['agent']['url'] + ('/Library/refresh?api_key=' + config['agent']['apiKey'] if config['agent']['type'] == 'emby' else '/ScheduledTasks/Running/6330ee8fb4a957f33981f89aa78b030f')
        if post(url, headers={'X-MediaBrowser-Token': config['agent']['apiKey']}).status_code < 300:
            functions.log('Succesfully updated ' + config['agent']['type'] + ' libraries (' + config['agent']['url'] + ')', 2, 2)
        else: functions.log('Error accessing ' + config['agent']['type'] + ' at ' + config['agent']['url'])
    else: functions.log('Not updating ' + config['agent']['type'] + ' library, Are api and url set?', 3, 3)
functions.log('DONE! Finished generating images in: ' + str(timedelta(seconds=round(time.time() - gstart))), 0, 0)

