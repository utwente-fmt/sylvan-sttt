#!/usr/bin/env python
from __future__ import print_function
import os
import sys
import re
from subprocess32 import call,TimeoutExpired
import math
import time
import random
import itertools
from tabulate import tabulate
import models as _m


class Experiment:
    NOTDONE=0
    DONE=1
    TIMEOUT=2

    def parse_logfile(self, handle):
        return None, None
    
    def get_status(self, filename):
        if (os.path.isfile(filename)):
            with open(filename, 'r') as res:
                res = self.parse_logfile(res)
                if res is not None: return Experiment.DONE, res['time'], res

        timeout_filename = "{}.timeout".format(filename)
        if (os.path.isfile(timeout_filename)):
            with open(timeout_filename, 'r') as to:
                return Experiment.TIMEOUT, int(to.read()), None

        return Experiment.NOTDONE, 0, None

    def run_experiment(self, timeout, filename):
        status, value, db = self.get_status(filename)
        if status == Experiment.DONE: return
        if status == Experiment.TIMEOUT and value >= int(timeout): return

        # remove output and timeout files
        if os.path.isfile(filename): os.unlink(filename)
        timeout_filename = "{}.timeout".format(filename)
        if os.path.isfile(timeout_filename): os.unlink(timeout_filename)

        print("Performing {}... ".format(self.name), end='')
        sys.stdout.flush()

        try:
            with open(filename, 'w+') as out:
                call(self.call, stdout=out, stderr=out, timeout=timeout)
        except KeyboardInterrupt:
            os.unlink(filename)
            print("interrupted!")
            sys.exit()
        except OSError:
            os.unlink(filename)
            print("OS failure! (missing executable?)")
            sys.exit()
        except TimeoutExpired:
            with open(timeout_filename, 'w') as to: to.write(str(timeout))
            print("timeout!")
        else:
            status, value, db = self.get_status(filename)
            if status == Experiment.DONE: print("done; {}!".format(value))
            else: print("not done!")
        time.sleep(2)


class ExperimentSet:
    def __init__(self, **kwargs):
        self.experiments = []
        if 'outdir' in kwargs: self.outdir = kwargs['outdir']
        else: self.outdir = 'out'
        if 'timeout' in kwargs: self.timeout = int(kwargs['timeout'])
        else: self.timeout = 1200

    def get_results(self):
        results = []
        timeouts = []
        yes = 0
        no = 0
        timeout = 0

        for i in itertools.count():
            stop = True
            for e in self.experiments:
                status, value, db = e.get_status("{}/{}-{}".format(self.outdir, e.name, i))
                if not status == Experiment.NOTDONE: stop = False
                if status == Experiment.DONE: results.append((e.name, db))
                elif status == Experiment.TIMEOUT: timeouts.append((e.name, value))
                else: no += 1
            if stop: return i, no - len(self.experiments), results, timeouts

    def run_experiments(self):
        if not os.path.exists(self.outdir): os.makedirs(self.outdir)
        for i in itertools.count():
            n, not_done, results, timeouts = self.get_results()
            print("In {} repetitions, {} succesful, {} timeouts, {} not done.".format(n, len(results), len(timeouts), not_done))

            print("Running iteration {}.".format(i))
            random.shuffle(self.experiments)
            for e in self.experiments: e.run_experiment(self.timeout, "{}/{}-{}".format(self.outdir, e.name, i))

    def append(self, experiment):
        self.experiments.append(experiment)


def online_variance(data):
    n = 0
    mean = 0
    M2 = 0

    for x in data:
        n = n + 1
        delta = x - mean
        mean = mean + delta/n
        M2 = M2 + delta*(x - mean)

    if n < 1: return n, float('nan'), float('nan')
    if n < 2: return n, mean, float('nan')

    variance = M2/(n - 1)
    return n, mean, variance


def fixnan(table):
    return [['--' if s.strip() == 'nan' else s for s in row] for row in table]


class ExperimentLTSmin(Experiment):
    def __init__(self, **kwargs):
        # order, workers, modelfile, name
        self.name = "{}-{}-{}".format(kwargs['name'], kwargs['order'], kwargs['workers'])
        self.call  = ["./dve2lts-sym", "--when", "-rgs"]
        self.call += ["--vset=lddmc", "--lddmc-tablesize=30", "--lddmc-cachesize=30"]
        self.call += ["--order={}".format(kwargs['order'])]
        self.call += ["--lace-workers={}".format(kwargs['workers'])]
        self.call += [kwargs['model']]

    def parse_logfile(self, handle):
        res = {}
        contents = handle.read()
        s = re.compile(r'reachability took ([\d\.,]+)').findall(contents)
        if len(s) != 1: return None
        res['time'] = float(s[0].replace(',',''))
        return res


class LTSminExperiments():
    workers = [1,48]
    #workers = [1,8,16,24,32,40,48]
    models = _m.models

    def __init__(self):
        for w in self.workers:
            setattr(self, 'pp_{}'.format(w), {})

        for m,a in self.models:
            filename = "models/{}.dve".format(m)
            for w in self.workers:
                getattr(self, 'pp_{}'.format(w))[m] = ExperimentLTSmin(name=m, order="par-prev", workers=w, model=filename)

    def add_to_set(self, experiments):
        for w in self.workers:
            experiments.experiments += getattr(self, 'pp_{}'.format(w)).values()

    def analyse(self, results, timeouts):
        res = {}
        for name in [name for name, fn in self.models]:
            r = {}
            for w in self.workers:
                nstr = 'n_pp_{}'.format(w)
                s = 'pp_{}'.format(w)
                dic = getattr(self, s)
                r[nstr], r[s] = online_variance([v['time'] for n, v in results if n==dic[name].name])[0:2]
                if w != 1:
                    if r[s] > 0: r['speedup_{}'.format(w)] = r['pp_1']/r[s]
                    else: r['speedup_{}'.format(w)] = float('nan')
            res[name] = r
        return res

    def report(self, results, timeouts):
        res = self.analyse(results, timeouts)
        table = []
        for name in sorted(res.keys()):
            r = res[name]
            row = [name]
            for w in self.workers:
                row += [str(r['n_pp_{}'.format(w)])]
            for w in self.workers:
                row += ["{:<6.2f}".format((r['pp_{}'.format(w)]))]
            for w in self.workers:
                if w != 1: row += ["{:<6.2f}".format((r['speedup_{}'.format(w)]))]
            table.append(row)

        headers = ["Model"]
        for w in self.workers:
            headers += ['N_{}'.format(w)]
        for w in self.workers:
            headers += ["T_{}".format(w)]
        for w in self.workers:
            if w != 1: headers += ["Speedup_{}".format(w)]

        print(tabulate(table, headers))

    def report_csv(self, results, timeouts, out):
        # write headers
        out.write("Model,Order,Workers,Value\n")
        res = self.analyse(results, timeouts)
        table = []
        for name in sorted(res.keys()):
            r = res[name]
            for w in self.workers:
                out.write("{},par-prev,{},{:<.2f}\n".format(name, w, r['pp_{}'.format(w)]))


class LTSminExperiments2(LTSminExperiments):
    workers = [1,8,16,24,32,40,48]
    models = [(m,a) for m,a in _m.models if m in ("collision.5", "blocks.4", "exit.4", "lann.6", "lifts.8", "mcs.5", "telephony.5", "rether.6")]

 
if __name__ == "__main__":
    engine = ExperimentSet(outdir='exp-out', timeout=1200)
    experiments = LTSminExperiments()
    experiments.add_to_set(engine)
    experiments2 = LTSminExperiments2()
    experiments2.add_to_set(engine)

    if len(sys.argv) > 1:
        if sys.argv[1] == 'run':
            engine.run_experiments()
        elif sys.argv[1] == 'report':
            n, no, results, timeouts = engine.get_results()
            experiments.report(results, timeouts)
            experiments2.report(results, timeouts)

            with open('results.csv', 'w') as f:
                experiments.report_csv(results, timeouts, f)

            with open('results-speedup.csv', 'w') as f:
                experiments2.report_csv(results, timeouts, f)
