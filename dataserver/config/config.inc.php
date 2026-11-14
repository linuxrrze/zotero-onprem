<?
// On-premise configuration for the Zotero dataserver.
// Based on include/config/config.inc.php-sample; values are taken from the
// environment (see docker-compose.yml / .env) at the bottom of this file.

class Z_CONFIG {
	public static $API_ENABLED = true;
	public static $READ_ONLY = false;
	public static $MAINTENANCE_MESSAGE = 'Server updates in progress. Please try again in a few minutes.';
	public static $BACKOFF = 0;

	// TESTING_SITE=false additionally rejects super-user requests from public IPs
	public static $TESTING_SITE = false;
	public static $DEV_SITE = false;

	public static $DEBUG_LOG = false;

	// URI namespace for object identifiers (relations, etc.). This is an
	// identifier, not a resolvable URL -- keep it identical to the client's
	// ZOTERO_CONFIG.BASE_URI so unmodified clients and the server agree.
	public static $BASE_URI = 'http://zotero.org/';
	public static $API_BASE_URI = '';      // from ZOTERO_API_URL
	public static $WWW_BASE_URI = '';      // from ZOTERO_WWW_URL (login page lives at <WWW>/login)

	public static $AUTH_SALT = '';
	public static $API_SUPER_USERNAME = '';
	public static $API_SUPER_PASSWORD = '';

	// S3-compatible object storage (Garage, Ceph RGW, SeaweedFS, ...)
	public static $AWS_REGION = 'us-east-1';
	public static $AWS_ACCESS_KEY = '';
	public static $AWS_SECRET_KEY = '';
	public static $AWS_SERVICE_CONFIG = [];  // set below (patch 0001)
	public static $S3_PUBLIC_ENDPOINT = '';  // set below (patch 0001)
	public static $S3_BUCKET = 'zotero';
	public static $S3_BUCKET_CACHE = '';
	public static $S3_BUCKET_FULLTEXT = 'zotero-fulltext';
	// Empty = no DynamoDB/Lambda full-text indexing pipeline (patch 0002)
	public static $FULLTEXT_INDEXING_TABLE = '';
	public static $S3_BUCKET_ERRORS = '';
	public static $SNS_ALERT_TOPIC = '';     // empty = alerts only go to the error log

	public static $REDIS_HOSTS = [
		'default' => ['host' => 'redis:6379'],
		'request-limiter' => ['host' => 'redis:6379'],
		'notifications' => ['host' => 'redis:6379'],
		'fulltext-migration' => ['host' => 'redis:6379', 'cluster' => false]
	];
	public static $REDIS_PREFIX = '';

	public static $MEMCACHED_ENABLED = true;
	public static $MEMCACHED_SERVERS = ['memcached:11211:1'];

	// Not needed by the desktop client (it runs translators locally)
	public static $TRANSLATION_SERVERS = [];
	public static $CITATION_SERVERS = [];

	// Elasticsearch is only used for server-side full-text search (API qmode=everything)
	public static $SEARCH_HOSTS = ['elasticsearch:9200'];

	public static $GLOBAL_ITEMS_URL = '';

	public static $ATTACHMENT_PROXY_URL = '';
	public static $ATTACHMENT_PROXY_SECRET = '';

	// Read Aloud (cloud TTS) is a zotero.org service -- not available on-premise
	public static $TTS_TABLE = '';
	public static $S3_BUCKET_TTS = '';
	public static $TTS_AUDIO_DOMAIN = '';
	public static $TTS_CREDIT_LIMITS = [
		'standard' => ['free' => 0, 'personal' => 0, 'institutional' => 0],
		'premium' => ['free' => 0, 'personal' => 0, 'institutional' => 0],
	];
	public static $TTS_DAILY_LIMIT_MINUTES = 0;

	public static $STATSD_ENABLED = false;
	public static $STATSD_PREFIX = "";
	public static $STATSD_HOST = "";
	public static $STATSD_PORT = 8125;

	public static $LOG_TO_SCRIBE = false;
	public static $LOG_ADDRESS = '';
	public static $LOG_PORT = 1463;
	public static $LOG_TIMEZONE = 'Europe/Berlin';
	public static $LOG_TARGET_DEFAULT = 'errors';

	public static $HTMLCLEAN_SERVER_URL = 'http://tinymce-clean:16342';

	public static $CLI_PHP_PATH = '/usr/local/bin/php';

	public static $ERROR_PATH = '/var/www/zotero/tmp/';

	public static $CACHE_VERSION_ATOM_ENTRY = 1;
	public static $CACHE_VERSION_BIB = 1;
	public static $CACHE_VERSION_RESPONSE_JSON_COLLECTION = 1;
	public static $CACHE_VERSION_RESPONSE_JSON_ITEM = 1;
	public static $CACHE_ENABLED_ITEM_RESPONSE_JSON = true;

	public static $REINDEX_QUEUE_URL = "";
}

// ---------------------------------------------------------------------------
// Environment overrides
// ---------------------------------------------------------------------------
(function () {
	$env = function ($name, $default = '') {
		$v = getenv($name);
		return ($v === false || $v === '') ? $default : $v;
	};
	$slash = function ($url) { return rtrim($url, '/') . '/'; };

	Z_CONFIG::$API_BASE_URI = $slash($env('ZOTERO_API_URL', 'http://localhost/'));
	Z_CONFIG::$WWW_BASE_URI = $slash($env('ZOTERO_WWW_URL', Z_CONFIG::$API_BASE_URI));

	Z_CONFIG::$AUTH_SALT = $env('ZOTERO_AUTH_SALT');
	Z_CONFIG::$API_SUPER_USERNAME = $env('ZOTERO_SUPER_USER');
	Z_CONFIG::$API_SUPER_PASSWORD = $env('ZOTERO_SUPER_PASSWORD');

	Z_CONFIG::$AWS_REGION = $env('S3_REGION', 'us-east-1');
	Z_CONFIG::$AWS_ACCESS_KEY = $env('S3_ACCESS_KEY');
	Z_CONFIG::$AWS_SECRET_KEY = $env('S3_SECRET_KEY');
	Z_CONFIG::$S3_BUCKET = $env('S3_BUCKET', 'zotero');
	Z_CONFIG::$S3_BUCKET_FULLTEXT = $env('S3_BUCKET_FULLTEXT', 'zotero-fulltext');
	Z_CONFIG::$S3_PUBLIC_ENDPOINT = $env('S3_PUBLIC_URL');
	Z_CONFIG::$AWS_SERVICE_CONFIG = [
		'S3' => [
			'endpoint' => $env('S3_INTERNAL_URL', 'http://garage:3900'),
			'use_path_style_endpoint' => true,
		],
	];

	Z_CONFIG::$DEBUG_LOG = (bool) $env('ZOTERO_DEBUG_LOG', '');

	if (!Z_CONFIG::$API_SUPER_USERNAME || !Z_CONFIG::$API_SUPER_PASSWORD) {
		throw new Exception("ZOTERO_SUPER_USER / ZOTERO_SUPER_PASSWORD must be set");
	}
})();
?>
