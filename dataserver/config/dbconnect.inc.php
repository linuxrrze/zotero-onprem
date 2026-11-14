<?
// Database connections for the Zotero dataserver (single MySQL instance).
// Shard hosts themselves are looked up in zotero_master.shardHosts; the
// 'shard' entry only provides credentials for them.
function Zotero_dbConnectAuth($db) {
	$host = getenv('MYSQL_HOST') ?: 'mysql';
	$port = (int) (getenv('MYSQL_PORT') ?: 3306);
	$user = getenv('MYSQL_USER');
	$pass = getenv('MYSQL_PASSWORD');
	$charset = '';
	$replicas = [];
	$state = 'up';

	if (!$user || !$pass) {
		throw new Exception("MYSQL_USER and MYSQL_PASSWORD must be set");
	}

	switch ($db) {
		case 'master':
			$db = 'zotero_master';
			break;

		case 'shard':
			// host/port/db come from the shardHosts/shards tables
			$host = false;
			$port = false;
			$db = false;
			break;

		case 'id1':
		case 'id2':
			$db = 'zotero_ids';
			break;

		case 'www1':
		case 'www2':
			$db = 'zotero_www';
			break;

		default:
			throw new Exception("Invalid db '$db'");
	}

	return [
		'host' => $host,
		'replicas' => $replicas,
		'port' => $port,
		'db' => $db,
		'user' => $user,
		'pass' => $pass,
		'charset' => $charset,
		'state' => $state
	];
}
?>
