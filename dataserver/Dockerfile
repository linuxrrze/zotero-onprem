# syntax=docker/dockerfile:1
#
# Zotero dataserver (https://github.com/zotero/dataserver), built from upstream
# source at a pinned commit, plus two small on-premise patches (see ./patches).
#
FROM php:8.4-apache-bookworm

ARG DATASERVER_REPO=https://github.com/zotero/dataserver.git
# Pinned upstream commit the patches were made against (2026-11-12).
ARG DATASERVER_REF=bdc2dc713ff3ceb26dc2d3e1b0f5c30dc1020627

# memcached is built from source with igbinary support: the dataserver sets
# Memcached::SERIALIZER_IGBINARY, which a plain "pecl install" leaves out.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        git unzip default-mysql-client curl ca-certificates \
        libicu-dev libzip-dev libxml2-dev libxslt1-dev libcurl4-openssl-dev \
        libmemcached-dev zlib1g-dev libssl-dev; \
    docker-php-ext-install -j"$(nproc)" mysqli pdo_mysql intl zip bcmath xsl pcntl sockets; \
    pecl install igbinary redis ds; \
    pecl bundle -d /tmp/pecl memcached; \
    ( cd /tmp/pecl/memcached && phpize && ./configure --enable-memcached-igbinary \
      && make -j"$(nproc)" && make install ); \
    docker-php-ext-enable igbinary memcached redis ds; \
    php -r 'exit(Memcached::HAVE_IGBINARY ? 0 : 1);'; \
    rm -rf /var/lib/apt/lists/* /tmp/pear /tmp/pecl

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer

# PHP settings required by the dataserver (it uses short open tags: "<?")
RUN { \
      echo 'short_open_tag = On'; \
      echo 'include_path = ".:/usr/local/lib/php:/var/www/zotero/include"'; \
      echo 'memory_limit = 1G'; \
      echo 'max_execution_time = 300'; \
      echo 'post_max_size = 64M'; \
      echo 'upload_max_filesize = 64M'; \
      echo 'display_errors = Off'; \
      echo 'log_errors = On'; \
      echo 'error_reporting = E_ALL & ~E_NOTICE & ~E_DEPRECATED'; \
    } > /usr/local/etc/php/conf.d/zotero.ini

# Fetch upstream source (incl. zotero-schema submodule) and apply patches
WORKDIR /var/www/zotero
RUN set -eux; \
    git init -q .; \
    git remote add origin "$DATASERVER_REPO"; \
    git fetch -q --depth 1 origin "$DATASERVER_REF"; \
    git checkout -q FETCH_HEAD; \
    git submodule update -q --init --depth 1
# Zend Framework 1 (only Zend_Db is used). Upstream expects it to be dropped
# into include/Zend by hand; the original ZF1 is EOL, so use the maintained
# PHP 8 compatible fork zf1-future.
ARG ZF1_VERSION=1.25.2
RUN set -eux; \
    curl -fsSL "https://codeload.github.com/Shardj/zf1-future/tar.gz/refs/tags/release-${ZF1_VERSION}" \
      | tar -xz -C include --strip-components=2 "zf1-future-release-${ZF1_VERSION}/library/Zend"; \
    test -f include/Zend/Db/Adapter/Mysqli.php

COPY patches/ /tmp/patches/
RUN set -eux; \
    for p in /tmp/patches/*.patch; do echo "Applying $p"; git apply --whitespace=nowarn "$p"; done; \
    composer install --no-dev --no-interaction --optimize-autoloader; \
    rm -rf .git

COPY config/config.inc.php config/dbconnect.inc.php /var/www/zotero/include/config/
COPY config/apache-zotero.conf /etc/apache2/sites-available/000-default.conf
COPY init/ /opt/zotero-init/

RUN set -eux; \
    a2enmod rewrite headers deflate remoteip; \
    mkdir -p /var/www/zotero/tmp/cache; \
    chown -R www-data:www-data /var/www/zotero/tmp; \
    chmod +x /opt/zotero-init/*.sh

EXPOSE 80
# Default: run Apache. The same image is used by the one-shot "init" service.
CMD ["apache2-foreground"]
