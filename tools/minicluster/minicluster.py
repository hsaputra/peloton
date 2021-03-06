#!/usr/bin/env python
"""
 -- Locally run and manage a personal cluster in containers.

This script can be used to manage (setup, teardown) a personal
Mesos cluster etc in containers, optionally Peloton
master or apps can be specified to run in containers as well.

@copyright:  2017 Uber Compute Platform. All rights reserved.

@license:    license

@contact:    peloton-dev@uber.com
"""

import os
import requests
import sys
import time
import yaml
from argparse import ArgumentParser
from argparse import RawDescriptionHelpFormatter
from collections import OrderedDict
from docker import Client

__date__ = '2016-12-08'
__author__ = 'wu'

max_retry_attempts = 20
sleep_time_secs = 5
healthcheck_path = '/health'
default_host = 'localhost'


class bcolors:
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    FAIL = '\033[91m'
    WARNING = '\033[93m'
    ENDC = '\033[0m'


def print_okblue(message):
    print bcolors.OKBLUE + message + bcolors.ENDC


def print_okgreen(message):
    print bcolors.OKGREEN + message + bcolors.ENDC


def print_fail(message):
    print bcolors.FAIL + message + bcolors.ENDC


def print_warn(message):
    print bcolors.WARNING + message + bcolors.ENDC


#
# Get container local ip.
# IP address returned is only reachable on the local machine and within
# the container.
#
def get_container_ip(container_name):
    return os.popen('''
        docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' %s
    ''' % container_name).read().strip()


#
# Load configs from file
#
def load_config():
    config_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "config.yaml")
    with open(config_file, "r") as f:
        config = yaml.load(f)
    return config


zk_url = None
cli = Client(base_url='unix://var/run/docker.sock')
work_dir = os.path.dirname(os.path.abspath(__file__))
config = load_config()


#
# Force remove container by name (best effort)
#
def remove_existing_container(name):
    try:
        cli.remove_container(name, force=True)
        print_okblue('removed container %s' % name)
    except Exception, e:
        if 'No such container' in str(e):
            return
        raise e


#
# Teardown mesos related containers.
#
def teardown_mesos():
    # 1 - Remove all Mesos Agents
    for i in range(0, config['num_agents']):
        agent = config['mesos_agent_container'] + repr(i)
        remove_existing_container(agent)

    # 2 - Remove Mesos Master
    remove_existing_container(config['mesos_master_container'])

    # 3- Remove orphaned mesos containers.
    for c in cli.containers(filters={'name': '^/mesos-'}, all=True):
        remove_existing_container(c.get("Id"))

    # 4 - Remove ZooKeeper
    remove_existing_container(config['zk_container'])


#
# Run mesos cluster
#
def run_mesos():
    # Remove existing containers first.
    teardown_mesos()

    # Run zk
    cli.pull(config['zk_image'])
    container = cli.create_container(
        name=config['zk_container'],
        hostname=config['zk_container'],
        host_config=cli.create_host_config(
            port_bindings={
                config['default_zk_port']: config['local_zk_port'],
            },
        ),
        image=config['zk_image'],
        detach=True
    )
    cli.start(container=container.get('Id'))
    print_okgreen('started container %s' % config['zk_container'])

    # TODO: add retry
    print_okblue('sleep 20 secs for zk to come up')
    time.sleep(20)

    # Run mesos master
    cli.pull(config['mesos_master_image'])
    container = cli.create_container(
        name=config['mesos_master_container'],
        hostname=config['mesos_master_container'],
        volumes=['/files'],
        ports=[repr(config['master_port'])],
        host_config=cli.create_host_config(
            port_bindings={
                config['master_port']: config['master_port'],
            },
            binds=[
                work_dir + '/files:/files',
                work_dir + '/mesos_config/etc_mesos-master:/etc/mesos-master'
            ],
            privileged=True
        ),
        environment=[
            'MESOS_AUTHENTICATE_HTTP_READWRITE=true',
            'MESOS_AUTHENTICATE_FRAMEWORKS=true',
            # TODO: Enable following flags for fully authentication.
            'MESOS_AUTHENTICATE_HTTP_FRAMEWORKS=true',
            'MESOS_HTTP_FRAMEWORK_AUTHENTICATORS=basic',
            'MESOS_CREDENTIALS=/etc/mesos-master/credentials',
            'MESOS_LOG_DIR=' + config['log_dir'],
            'MESOS_PORT=' + repr(config['master_port']),
            'MESOS_ZK=zk://{0}:{1}/mesos'.format(
                get_container_ip(config['zk_container']),
                config['default_zk_port']),
            'MESOS_QUORUM=' + repr(config['quorum']),
            'MESOS_REGISTRY=' + config['registry'],
            'MESOS_WORK_DIR=' + config['work_dir'],
        ],
        image=config['mesos_master_image'],
        entrypoint='bash /files/run_mesos_master.sh',
        detach=True,
    )
    cli.start(container=container.get('Id'))
    print_okgreen('started container %s' % config['mesos_master_container'])

    # Run mesos slaves
    cli.pull(config['mesos_slave_image'])
    for i in range(0, config['num_agents']):
        agent = config['mesos_agent_container'] + repr(i)
        port = config['local_agent_port'] + i
        container = cli.create_container(
            name=agent,
            hostname=agent,
            volumes=['/files', '/var/run/docker.sock'],
            ports=[repr(config['default_agent_port'])],
            host_config=cli.create_host_config(
                port_bindings={
                    config['default_agent_port']: port,
                },
                binds=[
                    work_dir + '/files:/files',
                    work_dir +
                    '/mesos_config/etc_mesos-slave:/etc/mesos-slave',
                    '/var/run/docker.sock:/var/run/docker.sock',
                ],
                privileged=True,
            ),
            environment=[
                'MESOS_PORT=' + repr(port),
                'MESOS_MASTER=zk://{0}:{1}/mesos'.format(
                    get_container_ip(config['zk_container']),
                    config['default_zk_port']
                ),
                'MESOS_SWITCH_USER=' + repr(config['switch_user']),
                'MESOS_CONTAINERIZERS=' + config['containers'],
                'MESOS_LOG_DIR=' + config['log_dir'],
                'MESOS_ISOLATION=' + config['isolation'],
                'MESOS_SYSTEMD_ENABLE_SUPPORT=false',
                'MESOS_IMAGE_PROVIDERS=' + config['image_providers'],
                'MESOS_IMAGE_PROVISIONER_BACKEND={0}'.format(
                    config['image_provisioner_backend']
                ),
                'MESOS_APPC_STORE_DIR=' + config['appc_store_dir'],
                'MESOS_WORK_DIR=' + config['work_dir'],
                'MESOS_RESOURCES=' + config['resources'],
                'MESOS_ATTRIBUTES=' + config['attributes'],
                'MESOS_MODULES=' + config['modules'],
                'MESOS_RESOURCE_ESTIMATOR=' + config['resource_estimator'],
                'MESOS_OVERSUBSCRIBED_RESOURCES_INTERVAL='
                + config['oversubscribed_resources_interval'],
                'MESOS_QOS_CONTROLLER=' + config['qos_controller'],
                'MESOS_QOS_CORRECTION_INTERVAL_MIN='
                + config['qos_correction_interval_min'],
            ],
            image=config['mesos_slave_image'],
            entrypoint='bash /files/run_mesos_slave.sh',
            detach=True,
        )
        cli.start(container=container.get('Id'))
        print_okgreen('started container %s' % agent)


#
# Run cassandra cluster
#
def run_cassandra():
    remove_existing_container(config['cassandra_container'])
    cli.pull(config['cassandra_image'])
    container = cli.create_container(
        name=config['cassandra_container'],
        hostname=config['cassandra_container'],
        host_config=cli.create_host_config(
            port_bindings={
                config['cassandra_cql_port']: config['cassandra_cql_port'],
                config['cassandra_thrift_port']:
                    config['cassandra_thrift_port'],
            },
            binds=[
                work_dir + '/files:/files',
            ],
        ),
        environment=['MAX_HEAP_SIZE=1G', 'HEAP_NEWSIZE=256M'],
        image=config['cassandra_image'],
        detach=True,
        entrypoint='bash /files/run_cassandra_with_stratio_index.sh',
    )
    cli.start(container=container.get('Id'))
    print_okgreen('started container %s' % config['cassandra_container'])

    # Create cassandra store
    create_cassandra_store()


#
# Create cassandra store with retries
#
def create_cassandra_store():
    retry_attempts = 0
    while retry_attempts < max_retry_attempts:
        time.sleep(sleep_time_secs)
        setup_exe = cli.exec_create(
            container=config['cassandra_container'],
            cmd='/files/setup_cassandra.sh',
        )
        show_exe = cli.exec_create(
            container=config['cassandra_container'],
            cmd='cqlsh -e "describe %s"' % config['cassandra_test_db'],
        )
        # by api design, exec_start needs to be called after exec_create
        # to run 'docker exec'
        resp = cli.exec_start(exec_id=setup_exe)
        if resp is "":
            resp = cli.exec_start(exec_id=show_exe)
            if "CREATE KEYSPACE peloton_test WITH" in resp:
                print_okgreen('cassandra store is created')
                return
        print_warn('failed to create cassandra store, retrying...')
        retry_attempts += 1

    print_fail('Failed to create cassandra store after %d attempts, '
               'aborting...'
               % max_retry_attempts)
    sys.exit(1)


#
# Run peloton
#
def run_peloton(applications):
    print_okblue('docker image "uber/peloton" has to be built first '
                 'locally by running IMAGE=uber/peloton make docker')

    for app, func in APP_START_ORDER.iteritems():
        if app in applications:
            should_disable = applications[app]
            if should_disable:
                continue
        APP_START_ORDER[app]()


#
# Starts a container and waits for it to come up
#
def start_and_wait(application_name, container_name, ports, extra_env=None):
    # TODO: It's very implicit that the first port is the HTTP port, perhaps we
    # should split it out even more.
    election_zk_servers = None
    mesos_zk_path = None
    if zk_url is not None:
        election_zk_servers = zk_url
        mesos_zk_path = 'zk://{0}/mesos'.format(zk_url)
    else:
        election_zk_servers = '{0}:{1}'.format(
            get_container_ip(config['zk_container']),
            config['default_zk_port'])
        mesos_zk_path = 'zk://{0}:{1}/mesos'.format(
            get_container_ip(config['zk_container']),
            config['default_zk_port'])
    env = {
        'CONFIG_DIR': 'config',
        'APP': application_name,
        'HTTP_PORT': ports[0],
        'DB_HOST': get_container_ip(config['cassandra_container']),
        'ELECTION_ZK_SERVERS': election_zk_servers,
        'MESOS_ZK_PATH': mesos_zk_path,
        'MESOS_SECRET_FILE': '/files/hostmgr_mesos_secret',
        'CASSANDRA_HOSTS': get_container_ip(config['cassandra_container']),
        'ENABLE_DEBUG_LOGGING': config['debug'],
        'DATACENTER': '',
        # used to migrate the schema;used inside host manager
        'AUTO_MIGRATE': config['auto_migrate'],
        'CLUSTER': 'minicluster',
    }
    if len(ports) > 1:
        env['GRPC_PORT'] = ports[1]
    if extra_env:
        env.update(extra_env)
    environment = []
    for key, value in env.iteritems():
        environment.append('%s=%s' % (key, value))
    # BIND_MOUNTS allows additional files to be mounted in the
    # the container. Expected format is a comma-separated list
    # of items of the form <host-path>:<container-path>
    mounts = os.environ.get("BIND_MOUNTS", "")
    mounts = mounts.split(",") if mounts else []
    container = cli.create_container(
        name=container_name,
        hostname=container_name,
        ports=[repr(port) for port in ports],
        environment=environment,
        host_config=cli.create_host_config(
            port_bindings={
                port: port
                for port in ports
            },
            binds=[
                work_dir + '/files:/files',
            ] + mounts,
        ),
        # pull or build peloton image if not exists
        image=config['peloton_image'],
        detach=True,
    )
    cli.start(container=container.get('Id'))
    wait_for_up(
        container_name,
        ports[0],  # use the first port as primary
    )


#
# Run peloton resmgr app
#
def run_peloton_resmgr():
    # TODO: move docker run logic into a common function for all apps to share
    for i in range(0, config['peloton_resmgr_instance_count']):
        # to not cause port conflicts among apps, increase port by 10
        # for each instance
        ports = [port + i * 10 for port in config['peloton_resmgr_ports']]
        name = config['peloton_resmgr_container'] + repr(i)
        remove_existing_container(name)
        start_and_wait('resmgr', name, ports)


#
# Run peloton hostmgr app
#
def run_peloton_hostmgr():
    for i in range(0, config['peloton_hostmgr_instance_count']):
        # to not cause port conflicts among apps, increase port
        # by 10 for each instance
        ports = [port + i * 10 for port in config['peloton_hostmgr_ports']]
        scarce_resource = ','.join(config['scarce_resource_types'])
        slack_resource = ','.join(config['slack_resource_types'])
        name = config['peloton_hostmgr_container'] + repr(i)
        remove_existing_container(name)
        start_and_wait('hostmgr', name, ports,
                       extra_env={'SCARCE_RESOURCE_TYPES': scarce_resource,
                                  'SLACK_RESOURCE_TYPES': slack_resource})


#
# Run peloton jobmgr app
#
def run_peloton_jobmgr():
    for i in range(0, config['peloton_jobmgr_instance_count']):
        # to not cause port conflicts among apps, increase port by 10
        #  for each instance
        ports = [port + i * 10 for port in config['peloton_jobmgr_ports']]
        name = config['peloton_jobmgr_container'] + repr(i)
        remove_existing_container(name)
        start_and_wait('jobmgr', name, ports,
                       extra_env={'MESOS_AGENT_WORK_DIR': config['work_dir'],
                                  'JOB_TYPE': os.getenv('JOB_TYPE', 'BATCH')})


#
# Run peloton aurora bridge app
#
def run_peloton_aurorabridge():
    for i in range(0, config['peloton_aurorabridge_instance_count']):
        ports = \
            [port + i * 10 for port in config['peloton_aurorabridge_ports']]
        name = config['peloton_aurorabridge_container'] + repr(i)
        remove_existing_container(name)
        start_and_wait('aurorabridge', name, ports)


#
# Run peloton placement app
#
def run_peloton_placement():
    i = 0
    for task_type in config['peloton_placement_instances']:
        # to not cause port conflicts among apps, increase port by 10
        # for each instance
        ports = [port + i * 10 for port in config['peloton_placement_ports']]
        name = config['peloton_placement_container'] + repr(i)
        remove_existing_container(name)
        start_and_wait('placement', name, ports,
                       extra_env={'TASK_TYPE': task_type})
        i = i + 1


#
# Run peloton archiver app
#
def run_peloton_archiver():
    for i in range(0, config['peloton_archiver_instance_count']):
        ports = [port + i * 10 for port in config['peloton_archiver_ports']]
        name = config['peloton_archiver_container'] + repr(i)
        remove_existing_container(name)
        start_and_wait('archiver', name, ports)


#
# Run health check for peloton apps
#
def wait_for_up(app, port):
    count = 0
    error = ''
    url = 'http://%s:%s/%s' % (
        default_host,
        port,
        healthcheck_path,
    )
    while count < max_retry_attempts:
        try:
            r = requests.get(url)
            if r.status_code == 200:
                print_okgreen('started %s' % app)
                return
        except Exception, e:
            print_warn('app %s is not up yet, retrying...' % app)
            error = str(e)
            time.sleep(sleep_time_secs)
            count += 1

    raise Exception('failed to start %s on %d after %d attempts, err: %s' %
                    (
                        app,
                        port,
                        max_retry_attempts,
                        error,
                    )
                    )


#
# Set up a personal cluster
#
def setup(disable_mesos=False, applications={}, enable_peloton=False):
    run_cassandra()
    if not disable_mesos:
        run_mesos()

    if enable_peloton:
        run_peloton(
            applications
        )


#
# Tear down a personal cluster
# TODO (wu): use docker labels when launching containers
#            and then remove all containers with that label
def teardown():
    # 1 - Remove jobmgr instances
    for i in range(0, config['peloton_jobmgr_instance_count']):
        name = config['peloton_jobmgr_container'] + repr(i)
        remove_existing_container(name)

    # 2 - Remove placement engine instances
    for i in range(0, len(config['peloton_placement_instances'])):
        name = config['peloton_placement_container'] + repr(i)
        remove_existing_container(name)

    # 3 - Remove resmgr instances
    for i in range(0, config['peloton_resmgr_instance_count']):
        name = config['peloton_resmgr_container'] + repr(i)
        remove_existing_container(name)

    # 4 - Remove hostmgr instances
    for i in range(0, config['peloton_hostmgr_instance_count']):
        name = config['peloton_hostmgr_container'] + repr(i)
        remove_existing_container(name)

    # 5 - Remove archiver instances
    for i in range(0, config['peloton_archiver_instance_count']):
        name = config['peloton_archiver_container'] + repr(i)
        remove_existing_container(name)

    # 6 - Remove aurorabridge instances
    for i in range(0, config['peloton_aurorabridge_instance_count']):
        name = config['peloton_aurorabridge_container'] + repr(i)
        remove_existing_container(name)

    teardown_mesos()

    remove_existing_container(config['cassandra_container'])


def parse_arguments():
    program_shortdesc = __import__('__main__').__doc__.split("\n")[1]
    program_license = '''%s

  Created by %s on %s.
  Copyright Uber Compute Platform. All rights reserved.

USAGE
''' % (program_shortdesc, __author__, str(__date__))
    # Setup argument parser
    parser = ArgumentParser(
        description=program_license,
        formatter_class=RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(help='command help', dest='command')
    # Subparser for the 'setup' command
    parser_setup = subparsers.add_parser(
        'setup',
        help='set up a personal cluster')
    parser_setup.add_argument(
        "--no-mesos",
        dest="disable_mesos",
        action='store_true',
        default=False,
        help="disable mesos setup"
    )
    parser_setup.add_argument(
        "--zk_url",
        dest="zk_url",
        action='store',
        type=str,
        default=None,
        help="zk URL when pointing to a pre-existing zk"
    )
    parser_setup.add_argument(
        "-a",
        "--enable-peloton",
        dest="enable_peloton",
        action='store_true',
        default=False,
        help="enable peloton",
    )
    parser_setup.add_argument(
        "--no-resmgr",
        dest="disable_peloton_resmgr",
        action='store_true',
        default=False,
        help="disable peloton resmgr app"
    )
    parser_setup.add_argument(
        "--no-hostmgr",
        dest="disable_peloton_hostmgr",
        action='store_true',
        default=False,
        help="disable peloton hostmgr app"
    )
    parser_setup.add_argument(
        "--no-jobmgr",
        dest="disable_peloton_jobmgr",
        action='store_true',
        default=False,
        help="disable peloton jobmgr app"
    )
    parser_setup.add_argument(
        "--no-placement",
        dest="disable_peloton_placement",
        action='store_true',
        default=False,
        help="disable peloton placement engine app"
    )
    parser_setup.add_argument(
        "--no-archiver",
        dest="disable_peloton_archiver",
        action='store_true',
        default=False,
        help="disable peloton archiver app"
    )
    parser_setup.add_argument(
        "--no-aurorabridge",
        dest="disable_peloton_aurorabridge",
        action='store_true',
        default=False,
        help="disable peloton aurora bridge app"
    )
    # Subparser for the 'teardown' command
    subparsers.add_parser('teardown', help='tear down a personal cluster')
    # Process arguments
    return parser.parse_args()


class App:
    """
    Represents the peloton apps
    """
    RESOURCE_MANAGER = 1
    HOST_MANAGER = 2
    PLACEMENT_ENGINE = 3
    JOB_MANAGER = 4
    ARCHIVER = 5
    AURORABRIDGE = 6


# Defines the order in which the apps are started
# NB: HOST_MANAGER is tied to database migrations so should be started first
APP_START_ORDER = OrderedDict([
    (App.HOST_MANAGER, run_peloton_hostmgr),
    (App.RESOURCE_MANAGER, run_peloton_resmgr),
    (App.PLACEMENT_ENGINE, run_peloton_placement),
    (App.JOB_MANAGER, run_peloton_jobmgr),
    (App.ARCHIVER, run_peloton_archiver),
    (App.AURORABRIDGE, run_peloton_aurorabridge)]
)


def main():
    args = parse_arguments()

    command = args.command

    if command == 'setup':
        applications = {
            App.HOST_MANAGER: args.disable_peloton_hostmgr,
            App.RESOURCE_MANAGER: args.disable_peloton_resmgr,
            App.PLACEMENT_ENGINE: args.disable_peloton_placement,
            App.JOB_MANAGER: args.disable_peloton_jobmgr,
            App.ARCHIVER: args.disable_peloton_archiver,
            App.AURORABRIDGE: args.disable_peloton_aurorabridge
        }

        global zk_url
        zk_url = args.zk_url
        setup(
            disable_mesos=args.disable_mesos,
            enable_peloton=args.enable_peloton,
            applications=applications
        )
    elif command == 'teardown':
        teardown()
    else:
        # Should never get here.  argparser should prevent it.
        print_fail('Unknown command: %s' % command)
        return 1


if __name__ == "__main__":
    main()
