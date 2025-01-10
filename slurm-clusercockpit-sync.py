#!/usr/bin/python3
# This script syncs the slurm jobs with the cluster cockpit backend. It uses 
# the slurm command line tools to gather the relevant slurm infos and reads
# the corresponding info from cluster cockpit via its api. 
# 
# After reading the data, it stops all jobs in cluster cockpit which are 
# not running any more according to slurm and afterwards it creates all new 
# running jobs in cluster cockpit. 
#
# -- Michael Schwarz <schwarz@uni-paderborn.de>

import subprocess
import json
import requests
import re

class CCApi:
    config = {}
    apiurl = ''
    apikey = ''
    headers = {}

    def __init__(self, config, debug=False):
        self.config = config
        self.apiurl = "%s/api/" % config['cc-backend']['host']
        self.apikey = config['cc-backend']['apikey']
        self.headers = { 'accept': 'application/ld+json', 
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer %s' % self.config['cc-backend']['apikey']}

    def startJob(self, data):
        url = self.apiurl+"jobs/start_job/"
        r = requests.post(url, headers=self.headers, json=data)
        if r.status_code == 201:
            return r.json()
        else:
            print(data)
            print(r)
            return False

    def stopJob(self, data):
        url = self.apiurl+"jobs/stop_job/"
        r = requests.post(url, headers=self.headers, json=data)
        if r.status_code == 200:
            return r.json()
        else:
            print(data)
            print(r)
            return False

    def getJobs(self, filter_running=True):
        url = self.apiurl+"jobs/?cluster="+self.config['clustername']+'&items-per-page=10000000'
        if filter_running:
            url = url+"&state=running"
        r = requests.get(url, headers=self.headers)
        if r.status_code == 200:
            return r.json()
        else:
            # If we didn't get valid data, raise exception and die
            r.raise_for_status()

class SlurmSync:
    slurmJobData = {}
    ccData = {}
    config = {}
    debug = False
    ccapi = None

    def __init__(self, config, debug=False):
        self.config = config
        self.debug = debug
        
        # validate config TODO
        if "slurm" not in config:
            raise KeyError
        if "squeue" not in config['slurm']:
            config.update({'squeue' : '/usr/bin/squeue'})
        if "sacct" not in config['slurm']:
            config.update({'sacct' : '/usr/bin/sacct'})
        if "cc-backend" not in config:
            raise KeyError
        if "host" not in config['cc-backend']:
            raise KeyError
        if "apikey" not in config['cc-backend']:
            raise KeyError

        self.ccapi = CCApi(self.config, debug)

    def _exec(self, command):
        process = subprocess.Popen(command, stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=True)
        output, error = process.communicate()
        if process.returncode == 0:
            return output.decode('utf-8')
        else:
            print("Error: ",error)
        return ""

    def _readSlurmData(self):
        if self.debug:
            print("DEBUG: _readSlurmData called")
        command = "%s --states R,CG --json" % self.config['slurm']['squeue']
        self.slurmJobData = json.loads(self._exec(command))

    def _readCCData(self):
        if self.debug:
            print("DEBUG: _readCCBackendData called")
        self.ccData = self.ccapi.getJobs()

    def _getAccDataForJob(self, jobid):
        command = "%s -j %s --json" % (self.config['slurm']['sacct'], jobid)
        return json.loads(self._exec(command))

    def _jobIdInCC(self, jobid):
        for job in self.ccData['jobs']:
            if jobid == job['jobId']:
                return True
        return False

    def _jobRunning(self, jobid):
        for job in self.slurmJobData['jobs']:
            if int(job['job_id']) == int(jobid):
                if job['job_state'][0] in ['RUNNING', 'COMPLETING']:
                    return True
        return False

    def _getACCIDsFromGRES(self, gres, nodename):
        ids = self.config['accelerators']

        nodetype = None
        for k, v in ids.items():
            if nodename.startswith(k):
                nodetype = k

        if not nodetype:
            print("WARNING: Can't find accelerator definition for node %s" % nodename.strip())
            return []

        # the gres definition might be different on other clusters!
        m = re.match(r"(fpga|gpu):(\w+):(\d)\(IDX:([\d,\-]+)\)", gres)
        if m:
            family = m.group(1)
            type = m.group(2)
            amount = m.group(3)
            indexes = m.group(4)
            acc_id_list = []

            # IDX might be: IDX:0,2-3
            # first split at , then expand the range to individual items
            if len(indexes) > 1:
                idx_list = indexes.split(',')
                idx = []
                for i in idx_list:
                    if len(i) == 1:
                        idx.append(i)
                    else:
                        start = i.split('-')[0]
                        end = i.split('-')[1]
                        idx = idx + list(range(int(start), int(end)+1))
                indexes = idx

            for i in indexes:
                acc_id_list.append(ids[nodetype][str(i)])

            return acc_id_list

        return []

    def _ccStartJob(self, job):
        print("INFO: Crate job %s, user %s, partition %s, name %s" % (job['job_id'], job['user_name'], job['partition'], job['name']))
        nodelist = self._convertNodelist(job['job_resources']['nodes'])

        # Exclusive job?
        if job['shared'] == "none":
            exclusive = 1
        # exclusive to user
        elif job['shared'] == "user":
            exclusive = 2
        # exclusive to mcs
        elif job['shared'] == "mcs":
            exclusive = 3
        # default is shared node
        else:
            exclusive = 0

        # read job script and environment
        hashdir = "hash.%s" % str(job['job_id'])[-1]
        jobscript_filename = "%s/%s/job.%s/script" % (self.config['slurm']['state_save_location'], hashdir, job['job_id'])
        jobscript = ''
        try:
            with open(jobscript_filename, 'r', encoding='utf-8') as f:
                jobscript = f.read()
        except FileNotFoundError:
            jobscript = 'NO JOBSCRIPT'

        environment = ''
        # FIXME sometimes produces utf-8 conversion errors
        environment_filename = "%s/%s/job.%s/environment" % (self.config['slurm']['state_save_location'], hashdir, job['job_id'])
        for enc in ['utf-8', 'utf-16', 'utf-32']:
            if environment == '' or environment == 'UNICODE_DECODE_ERROR':
                try:
                    with open(environment_filename, 'r', encoding=enc) as f:
                        environment = f.read()
                except FileNotFoundError:
                    environment = 'NO ENV'
                except UnicodeDecodeError:
                    environment = 'UNICODE_DECODE_ERROR'
                except UnicodeError:
                    environment = 'UNICODE_DECODE_ERROR'


        # get additional info from slurm and add environment
        command = "%s show job %s --detail" % (self.config['slurm']['scontrol'], job['job_id'])
        slurminfo = self._exec(command)
        slurminfo = slurminfo + "ENV:\n====\n" + environment
        # truncate environment. Otherwise it might not fit into the
        # Database field together with the job script etc.
        if len(slurminfo) > 50000:
            slurminfo = slurminfo[:50000]
            slurminfo = slurminfo + "\n === TRUNCATED ==="

        # build payload
        data = {'jobId' : job['job_id'],
            'user' : job['user_name'],
            'cluster' : self.config['clustername'],
            'numNodes' : job['node_count']['number'],
            'numHwthreads' : job['cpus']['number'],
            'startTime': job['start_time']['number'],
            'walltime': int(job['time_limit']['number']) * 60,
            'project': job['account'],
            'partition': job['partition'],
            'exclusive': exclusive,
            'resources': [],
            'metadata': { 
                'jobName' : job['name'], 
                'jobScript' : jobscript, 
                'slurmInfo' : slurminfo
                }
            }

        # is this part of an array job?
        if job['array_job_id']['set'] and job['array_job_id']['number'] > 0:
            data.update({"arrayJobId" : job['array_job_id']['number']})

        i = 0
        num_acc = 0
        for node in nodelist:
            # begin dict
            resources = {'hostname' : node.strip()}

            # if a job uses a node exclusive, there are some assigned cpus (used by this job)
            # and some unassigned cpus. In this case, the assigned_cpus are those which have
            # to be monitored, otherwise use the unassigned cpus. 
            sockets = job['job_resources']['allocated_nodes'][i]['sockets']
            hwthreads = []
            for socket,cores in sockets.items():
                for cid, state in cores['cores'].items():
                    if state == 'allocated':
                        hwthreads.append(int(cid) + int(socket) * self.config['nodes']['cores_per_socket'])

            resources.update({"hwthreads": hwthreads})

            # Get allocated GPUs if some are requested
            if len(job['gres_detail']) > 0:
                
                gres = job['gres_detail'][i]
                acc_ids = self._getACCIDsFromGRES(gres, node)
                if len(acc_ids) > 0:
                    num_acc = num_acc + len(acc_ids)
                    resources.update({"accelerators" : acc_ids})

            data['resources'].append(resources)
            i = i + 1

        # if the number of accelerators has changed in the meantime, upate this field
        data.update({"numAcc" : num_acc})

        if self.debug:
            print(data)

        self.ccapi.startJob(data)
        
    def _ccStopJob(self, jobid):
        print("INFO: Stop job %s" % jobid)
        
        # get search for the jobdata stored in CC
        ccjob = {}
        for j in self.ccData['jobs']:
            if j['jobId'] == jobid:
                ccjob = j

        jobInSqueue = False
        # check if job is still in squeue data
        for job in self.slurmJobData['jobs']:
            if job['job_id'] == jobid:
                jobInSqueue = True
                jobstate = job['job_state'][0].lower()
                endtime = job['end_time']['number']
                if jobstate not in ['running', 'completed', 'failed', 'cancelled', 'stopped', 'timeout', 'preempted', 'out_of_memory' ]:
                    jobstate = 'failed'

                if int(ccjob['startTime']) >= int(job['end_time']['number']):
                    print("squeue correction")
                    # For some reason (needs to get investigated), failed jobs sometimes report 
                    # an earlier end time in squee than the starting time in CC. If this is the
                    # case, take the starting time from CC and add ten seconds to the starting 
                    # time as new end time. Otherwise CC refuses to end the job.
                    endtime = int(ccjob['startTime']) + 1                    

        if not jobInSqueue:
            jobsAcctData = self._getAccDataForJob(jobid)['jobs']
            for j in jobsAcctData:
                if len(j['steps']) > 0 and j['steps'][0]['time']['start']['number'] == ccjob['startTime']:
                    jobAcctData = j
            jobstate = jobAcctData['state']['current'][0].lower()
            endtime = jobAcctData['time']['end']

            if jobstate not in ['running', 'completed', 'failed', 'cancelled', 'stopped', 'timeout', 'preempted', 'out_of_memory' ]:
                jobstate = 'failed'

            if int(ccjob['startTime']) >= int(jobAcctData['time']['end']):
                print("sacct correction")
                # For some reason (needs to get investigated), failed jobs sometimes report 
                # an earlier end time in squee than the starting time in CC. If this is the
                # case, take the starting time from CC and add ten seconds to the starting 
                # time as new end time. Otherwise CC refuses to end the job.
                endtime = int(ccjob['startTime']) + 1

        data = {
            'jobId' : jobid,
            'cluster' : ccjob['cluster'],
            'startTime' : ccjob['startTime'],
            'stopTime' : endtime,
            'jobState' : jobstate
        }

        self.ccapi.stopJob(data)

    def _convertNodelist(self, nodelist):
        # Use slurm to convert a nodelist with ranges into a comma separated list of unique nodes
        if re.search(self.config['node_regex'], nodelist):
            command = "%s show hostname %s | paste -d, -s" % (self.config['slurm']['scontrol'], nodelist)
            retval = self._exec(command).split(',')
            return retval
        else:
            return []


    def sync(self, limit=200, jobid=None, direction='both'):
        if self.debug:
            print("DEBUG: sync called")
            print("DEBUG: jobid %s" % jobid)
        self._readSlurmData()
        self._readCCData()

        # Abort after a defined count of sync actions. The intend is, to restart this script after the
        # limit is reached. Otherwise, if many many jobs get stopped, the script might miss some new jobs.
        sync_count = 0

        # iterate over cc jobs and stop them if they have already ended
        if direction in ['both', 'stop']:
            for job in self.ccData['jobs']:
                if jobid:
                    if int(job['jobId']) == int(jobid) and not self._jobRunning(job['jobId']):
                        self._ccStopJob(job['jobId'])
                        sync_count = sync_count + 1
                else:
                    if not self._jobRunning(job['jobId']):
                        self._ccStopJob(job['jobId'])
                        sync_count = sync_count + 1
                if sync_count >= limit:
                    print("INFO: sync limit (%s) reached" % limit)
                    break

        sync_count = 0
        # iterate over running jobs and add them to cc if they are still missing there
        if direction in ['both', 'start']:
            for job in self.slurmJobData['jobs']:
                # Skip this job if the user does not want the metadata of this job to be submitted to ClusterCockpit
                # The text field admin_comment is used for this. We assume that this field contains a comma seperated 
                # list of flags.
                if "disable_cc_submission" in job['admin_comment'].split(','):
                    print("INFO: Job %s: disable_cc_sumbission is set. Continue with next job" % job['job_id'])
                    continue
                # consider only running jobs
                if job['job_state'] == [ "RUNNING" ]:
                    if jobid:
                        if int(job['job_id']) == int(jobid) and not self._jobIdInCC(job['job_id']):
                            self._ccStartJob(job)
                            sync_count = sync_count + 1
                    else:
                        if not self._jobIdInCC(job['job_id']):
                            self._ccStartJob(job)
                            sync_count = sync_count + 1
                if sync_count >= limit:
                    print("INFO: sync limit (%s) reached" % limit)
                    break

if __name__ == "__main__":
    import argparse
    about = """This script syncs the slurm jobs with the cluster cockpit backend. It uses 
        the slurm command line tools to gather the relevant slurm infos and reads
        the corresponding info from cluster cockpit via its api. 

        After reading the data, it stops all jobs in cluster cockpit which are 
        not running any more according to slurm and afterwards it creates all new 
        running jobs in cluster cockpit. 
        """
    parser = argparse.ArgumentParser(description=about)
    parser.add_argument("-c", "--config", help="Read config file. Default: config.json", default="config.json")
    parser.add_argument("-d", "--debug", help="Enable debug output", action="store_true")
    parser.add_argument("-j", "--jobid", help="Sync this jobid")
    parser.add_argument("-l", "--limit", help="Stop after n sync actions in each direction. Default: 200", default="200", type=int)
    parser.add_argument("--direction", help="Only sync in this direction", default="both", choices=['both', 'start', 'stop'])
    args = parser.parse_args()

    # open config file
    if args.debug:
        print("DEBUG: load config file: %s" % args.config)
    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    if args.debug:
        print("DEBUG: config file contents:")
        print(config)

    s = SlurmSync(config, args.debug)
    s.sync(args.limit, args.jobid, args.direction)
