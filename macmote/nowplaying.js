// Now-playing information and transport commands, run as `osascript -l JavaScript nowplaying.js`.
//
// Since macOS 15.4 the MediaRemote framework only answers processes with a com.apple.* bundle
// identifier, so the server does not call it from its own helper: osascript is such a process
// and loads the framework here through the ObjC bridge. Output is one JSON document on stdout.
//
//   nowplaying.js            -> {"title", "artist", "album", "app", "playing", "elapsed", "duration"} or null
//   nowplaying.js send N     -> {"sent": N}   (0 play, 1 pause, 2 toggle, 4 next, 5 previous)
ObjC.import('Foundation');

function run(argv) {
  const bundle = $.NSBundle.bundleWithPath('/System/Library/PrivateFrameworks/MediaRemote.framework/');
  if (!bundle.load) return JSON.stringify({ error: 'MediaRemote did not load' });
  const Request = $.NSClassFromString('MRNowPlayingRequest');
  if (argv[0] === 'send') {
    const controller = $.NSClassFromString('MRNowPlayingController').localRouteController;
    const code = parseInt(argv[1], 10);
    controller.sendCommandOptionsCompletion(code, $.NSDictionary.dictionary, null);
    $.NSThread.sleepForTimeInterval(0.4);  // the send is asynchronous; let it leave before exiting
    return JSON.stringify({ sent: code });
  }
  const item = Request.localNowPlayingItem;
  if (item.isNil()) return 'null';
  const info = item.nowPlayingInfo;
  const get = (key) => { const value = info.valueForKey(key); return value.isNil() ? null : value.js; };
  const path = Request.localNowPlayingPlayerPath;
  const metadata = item.metadata;
  return JSON.stringify({
    title: get('kMRMediaRemoteNowPlayingInfoTitle'),
    artist: get('kMRMediaRemoteNowPlayingInfoArtist'),
    album: get('kMRMediaRemoteNowPlayingInfoAlbum'),
    app: path.isNil() ? null : path.client.displayName.js,
    playing: get('kMRMediaRemoteNowPlayingInfoPlaybackRate') === 1,
    elapsed: metadata.isNil() ? 0 : metadata.calculatedPlaybackPosition,
    duration: get('kMRMediaRemoteNowPlayingInfoDuration') || 0,
  });
}
