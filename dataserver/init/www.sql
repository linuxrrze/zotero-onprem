-- Minimal "zotero_www" schema.
--
-- On zotero.org these tables belong to the (closed-source) website. The
-- dataserver only reads them: user accounts and passwords, e-mail addresses,
-- profile metadata, institutional storage quotas and ban flags. This schema
-- covers every query the dataserver (and the login service) performs.

CREATE TABLE IF NOT EXISTS `users` (
  `userID` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `username` varchar(255) NOT NULL,
  -- bcrypt ($2y$...) recommended; legacy sha1(AUTH_SALT+pw) and md5 are also accepted
  `password` varchar(255) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
  `email` varchar(255) NOT NULL DEFAULT '',
  `role` enum('normal','deleted') NOT NULL DEFAULT 'normal',
  -- 'local' = password stored here, 'ldap' = verified against the directory
  `authSource` varchar(20) NOT NULL DEFAULT 'local',
  `dateAdded` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`userID`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `users_email` (
  `emailID` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `userID` int(10) unsigned NOT NULL,
  `email` varchar(255) NOT NULL,
  `validated` tinyint(1) NOT NULL DEFAULT 1,
  `dateAdded` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`emailID`),
  KEY `userID` (`userID`),
  KEY `email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `users_meta` (
  `userID` int(10) unsigned NOT NULL,
  `metaKey` varchar(60) NOT NULL,
  `metaValue` text NOT NULL,
  `lastUpdated` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`userID`,`metaKey`),
  KEY `metaKey` (`metaKey`,`metaValue`(20))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Web session lookup (website cookie auth) -- unused on-premise but queried
CREATE TABLE IF NOT EXISTS `sessions` (
  `id` varchar(128) NOT NULL,
  `userID` int(10) unsigned NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Forum ban flags (queried when adding group members)
CREATE TABLE IF NOT EXISTS `GDN_User` (
  `UserID` int(10) unsigned NOT NULL,
  `Banned` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`UserID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Institutional storage quotas. A row matches users whose *validated* e-mail
-- domain matches `domain` (a regexp, implicit optional subdomain prefix).
CREATE TABLE IF NOT EXISTS `storage_institutions` (
  `institutionID` smallint(5) unsigned NOT NULL AUTO_INCREMENT,
  `domain` varchar(100) NOT NULL,
  `domainBlacklist` varchar(255) NOT NULL DEFAULT '',
  `storageQuota` int(11) NOT NULL,   -- MB
  PRIMARY KEY (`institutionID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `storage_institution_email` (
  `institutionID` smallint(5) unsigned NOT NULL,
  `email` varchar(255) NOT NULL,
  PRIMARY KEY (`institutionID`,`email`),
  KEY `email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
