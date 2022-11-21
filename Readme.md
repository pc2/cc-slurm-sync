# Introduction

This script syncs the slurm jobs with the 
[cluster cockpit](https://github.com/ClusterCockpit/) backend. It uses the
slurm command line tools to gather the relevant slurm infos and reads the
corresponding info from cluster cockpit via its api. After reading the data,
it stops all jobs in cluster cockpit which are not running any more according
to slurm and afterwards it creates all new running jobs in cluster cockpit.

The script has to run on the slurm controller node and needs permissions 
to run the slurm commands squeue and sacct and read data from the slurm
state save location.

# Requirements

This script expects a certain data structure in the output of squeue. We have 
noticed during development that `squeue --json` does not distinguish between 
individual CPUs in the resources used and in the output the allocation of CPU 1 
and 2 is considered to be the same. However, this may be different for shared 
nodes if multiple jobs request a certain set of resources.

The cause can be found in the code of the API interface and is based on the 
fact that the core ID is taken modulo the number of cores on a socket. 

The included patch corrects this behavior. It is necessary to recompile slurm 
for this. The patch is for openapi 0.0.37 but should work with other versions
as well. 

# Getting started

The easiest way is to clone the Git repository. This way you always get the latest updates. 

    git clone https://github.com/pc2/cc-slurm-sync.git
    cd cc-slurm-sync

## Configuration file
Before you start, you have to create a configuration file. You can use 
`config.json.example` as a starting point. Simply copy or rename it to
`config.json`.

### Confiuration options
**clustername**
Type in your clustername here. This value must be the same as the value used in cc-backend to identify the cluster. It might be different to the cluster name used in slurm.

**slurm**
* `squeue` Path to the squeue binary. Defaults to `/usr/bin/squeue`
* `sacct` Path to the sacct binary. Defaults to `/usr/bin/sacct`
* `state_save_location` Statesave location of slurm. This option has no default value and is **mandatory**.

**cc-backend**
* `host` The url of the cc-backend api. Must be a valid url excluding trailing `/api`. This option is **mandatory**.
* `apikey` The JWT token to authenticate against cc-backend. This option is **mandatory**.

**accelerators**

This part describes accelerators which might be used in jobs. The format is as follows:

	"accelerators" : {
		"n2gpu" : {
			"0": "00000000:03:00.0",
			"1": "00000000:44:00.0",
			"2": "00000000:84:00.0",
			"3": "00000000:C4:00.0"
		},
		"n2dgx" : {
			"0": "00000000:07:00.0",
			"1": "00000000:0F:00.0",
			"2": "00000000:47:00.0",
			"3": "00000000:4E:00.0",
			"4": "00000000:87:00.0",
			"5": "00000000:90:00.0",
			"6": "00000000:B7:00.0",
			"7": "00000000:BD:00.0"
		}
	},

The first level (`n2gpu`) describes the prefix of the host names in which corresponding accelerators are installed. The second level describes the ID in Slurm followed by the device id.

How to get this data? It depends on the accelerators. The following example is for a host with four NVidia A100 GPUs. This should be similar on all hosts with NVidia GPUs:

    # nvidia-smi 
    Thu Aug 25 14:50:05 2022       
    +-----------------------------------------------------------------------------+
    | NVIDIA-SMI 510.47.03    Driver Version: 510.47.03    CUDA Version: 11.6     |
    |-------------------------------+----------------------+----------------------+
    | GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
    | Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
    |                               |                      |               MIG M. |
    |===============================+======================+======================|
    |   0  NVIDIA A100-SXM...  On   | 00000000:03:00.0 Off |                    0 |
    | N/A   58C    P0   267W / 400W |   7040MiB / 40960MiB |     88%      Default |
    |                               |                      |             Disabled |
    +-------------------------------+----------------------+----------------------+
    |   1  NVIDIA A100-SXM...  On   | 00000000:44:00.0 Off |                    0 |
    | N/A   59C    P0   337W / 400W |   7040MiB / 40960MiB |     96%      Default |
    |                               |                      |             Disabled |
    +-------------------------------+----------------------+----------------------+
    |   2  NVIDIA A100-SXM...  On   | 00000000:84:00.0 Off |                    0 |
    | N/A   57C    P0   266W / 400W |   7358MiB / 40960MiB |     89%      Default |
    |                               |                      |             Disabled |
    +-------------------------------+----------------------+----------------------+
    |   3  NVIDIA A100-SXM...  On   | 00000000:C4:00.0 Off |                    0 |
    | N/A   56C    P0   271W / 400W |   7358MiB / 40960MiB |     89%      Default |
    |                               |                      |             Disabled |
    +-------------------------------+----------------------+----------------------+

You will find the four GPUs identified by ids starting at 0. In the second coloum, you can find the Bus-ID or identifier of the GPU. These are the values which have to be defined in the code example above. The mechanism in the background assumes that all nodes starting with this prefix have the same configuration and assignment of ID to bus ID. So if you have another configuration, you have to start a new prefix, only matching the hosts with this configuration.

**node_regex**

This option is unique to every cluster system. This regex describes the sytax of the hostnames which are used as computing resources in jobs. \ have to be escaped

Example: `^(n2(lcn|cn|fpga|gpu)[\\d{2,4}\\,\\-\\[\\]]+)+$`

## Running the script

Simply run `slurm-clusercockpit-sync.py` inside the same directory which contains the config.json file. A brief help is also available:

* `-c, --config` You can use a different config file for testing or other purposes. Otherwise it would use config.json in the actual directory.
* `-j, --jobid` In a test setup it might be useful to sync individual job ids instead of syncing all jobs.
* `-l, --limit` Synchronize only this number of jobs in the respective direction. Stopping a job might take some short time. If a massive amount of jobs have to get stopped, the script might run a long time and miss new starting jobs if they start end end within the execution time of the script. 
* `--direction` Mostly a debug option. Only synchronize starting or stopping jobs. The default is both directions.

The script terminates after synchronization of all jobs. 

# Getting help

This script is to be seen as an example implementation and may have to be adapted for other installations. I tried to keep the script as general as possible and to catch some differences between clusters already. If adjustments are necessary, I am happy about pull requests or notification about that on other ways to get an implementation that runs on as many systems as possible without adjustments in the long run.
