import csv
import os
import random
import string

import networkx as nx

from metachem import Template, CoreNode, CoreContainer, CoreControl, Simulate, ParticleFactory, Particle
from metachem.StringCatChem import SCCBond
from metachem.RBNworld import WatsonRBNParticleFactory, RBNSpikeyWatsonBond, RBNParticle, WatsonSpike


class WellMixedTank(Template):

    def __init__(self, bondgraph, sample_size=2, reactions=100, generations=10000, tank_size=1000, load_type=None,
                 log_node=None, load_file=None):
        """
        Simulates a well mixed tank approach to an artificial chemistry. It generates a single tank of particles. In
        each generation it attempts the number of reactions requested by sampling the correct number of particles and
        handing it to the bondgraph. It places the returned particles back in to the tank.

        Parameters
        ----------

        bondgraph   :   Subgraph
            The metachem graph that will perform bonding. Must have a single link container and
            a single control node in and out.
        sample_size :   int
            The number of particles placed in the sample for the bonding graph.
        reactions   :   int
            The number of bonding reactions to perform each generation.
        generations :   int
            The number of generations to perform before exiting the simulation.
        tank_size   :   int
            The number of particles to put in the initial tank.
        load_type   :   String
            The type of initial tank to read/generate. "scc" - Uppercase letters, "int" - integers,
            "csv" - read from csv file, "RBN" - Watson RBNs
        log_node    :   CoreNode.Observer
            A node used to log information from the simulation
        load_file   :   String
            The file path for csv file if reading in initial tank.
        """
        super(WellMixedTank, self).__init__(bondgraph)
        # check bondgraph meets requirements for well mixed tank
        if not (
                bondgraph.count_control_in == 1 and bondgraph.count_control_out == 1 and bondgraph.count_links == 1):
            raise ValueError("WellMixedTank requires the bond graph have a single link container and a single in and "
                             "a single out control node")

        # generate graph with bondgraph connected

        # set up containers
        TTank = CoreContainer.ListTank(self.graph)
        self.tank = TTank
        TNew = CoreContainer.ListTank(self.graph)
        VTime = CoreContainer.StackEnvironment(self.graph)
        VTime.add(0)
        VGen = CoreContainer.StackEnvironment(self.graph)
        VGen.add(0)
        VBond = CoreContainer.DictionaryEnvironment(self.graph)
        VReaction = CoreContainer.DataFrameEnvironment(self.graph, columns=["id1", "id2", "obj1", "obj2", "Prods", "Open_Spikes", "Bonded_Spikes", "Intensity", "Generation"])
        self.reactions = VReaction
        # connect SComposite to link containers
        SComposite = CoreContainer.ListSample(self.graph)
        for link in bondgraph.links:
            link.set_linknode(SComposite)
        # if log included add log environment
        VLog = CoreContainer.DictionaryEnvironment(self.graph)

        # set up control nodes

        sload = LoadSampler(self.graph, containersout=TTank, tank_size=tank_size,
                            load_type=load_type, load_file=load_file)
        self.start = sload
        ssample = CoreControl.SimpleSampler(self.graph, TTank, SComposite, size=sample_size)
        sreturn = CoreControl.BruteSampler(self.graph, SComposite, TNew)
        sgeneration = CoreControl.BruteSampler(self.graph, TNew, TTank)
        ssimulation = CoreControl.BruteSampler(self.graph, TNew, TTank)
        tterm = CoreNode.Termination(self.graph)
        otime = CoreControl.ClockObserver(self.graph, VTime, VTime)
        oreset = CoreControl.ClockResetObserver(self.graph, VGen, VGen)
        ogen = CoreControl.ClockObserver(self.graph, VGen, VGen)
        dgen = TimingsDecision(self.graph, [VGen, VTime, TTank], generations, reactions, sample_size)
        osample = SampleObserver(self.graph, VBond, VBond, SComposite)
        oreturn = ReturnObserver(self.graph, VBond, VReaction, VGen, SComposite)
        # if log_node given include
        if log_node:
            olog = log_node
            self.graph.add_node(olog)
        else:
            olog = TimeLoggerObserver(self.graph, [VTime, VGen], [VTime, VGen])

        # Creat stable graph edges
        edges = [[sload, otime], [otime, oreset], [oreset, ssample], [ssample, osample], [oreturn, sreturn],
                 [sreturn, ogen], [ogen, dgen], [dgen, ssample], [dgen, sgeneration], [sgeneration, olog],
                 [olog, otime], [dgen, ssimulation], [ssimulation, tterm]]

        # add edges to graph
        for edge in edges:
            self.graph.add_edge(edge[0], edge[1])

        # connect bonding graph
        self.graph = nx.compose(self.graph, bondgraph.graph)
        # connect control in and out
        self.graph.add_edge(osample, bondgraph.control_in[0])
        self.graph.add_edge(bondgraph.control_out[0], oreturn)
        # connect link node
        bondgraph.links[0].set_linknode(SComposite)

    def print_tank(self):
        """
        Prints the contents of the main tank.

        """
        stuff = self.tank.read()
        if isinstance(stuff[0], Particle):
            print([part.id for part in stuff])
        else:
            print(self.tank.read())

    def print_reactions(self):
        print(self.reactions.read())

    def results_to_csv(self):
        """
                Writes the reactions data to a CSV file.

                Parameters:
                filename (str): The name of the CSV file to write to.
                """
        suffix = 0
        while True:
            if suffix == 0:
                file_path = "Outputs/output.csv"
            else:
                base_name, extension = os.path.splitext("Outputs/output.csv")
                file_path = f"{base_name}_{suffix}{extension}"
            if not os.path.exists(file_path):
                break
            suffix += 1

        with open(file_path, mode='w', newline='') as file:
            self.reactions.read().to_csv(file, index=False)

        return file_path

class LoadSampler(CoreNode.Sampler):

    def __init__(self, graph, containersout=None, tank_size=1000,
                 load_type="int", load_file=None):
        """
        Loads initial state of tank for the system. This can be read in from a csv file, or it can generate a tank.
        Generated tanks can hold:
        'scc' - Uppercase letters
        'int' - integers from 0 to 100

        Parameters
        ----------
        graph           :   networkx.DiGraph
            Graph the node will be added to.
        containersout   :   Tank
            Tank for the generated or read in particles to be put into.
        tank_size       :   int
            Number of particles in the initial tank.
        load_type       :   String/ParticleFactory
            How the tank should be generated: read from csv (csv), generate uppercase letters (scc),
            generate ints (int), generate WatsonRBN particles (RBN). It can also take an initialised ParticleFactory
            which will then be called to generate enough particles to fill the tank
        load_file       :   String
            File path to csv file to read in
        """
        super(LoadSampler, self).__init__(graph, CoreNode.Sample(graph), containersout)
        self.load_file = load_file
        self.load_type = load_type
        self.tank_size = tank_size

    def read(self):
        """
        If reading from file reads in the information as a flat list.

        """
        # csv read
        if self.load_type == "csv":
            file = open(self.load_file, "r")
            self.sample = list(csv.reader(file, delimiter=","))[0]
            file.close()

    def pull(self):
        """
        Generates Tanks of the correct size if using generators.

        """
        # gen values if not read
        if self.load_type == "scc":
            self.sample = [random.choice(string.ascii_uppercase) for _ in range(0, self.tank_size)]
        elif self.load_type == "int":
            self.sample = [random.randint(0, 100) for _ in range(0, self.tank_size)]
        elif self.load_type == "RBN":
            factory = WatsonRBNParticleFactory(8, 2)
            self.sample = factory.createParticles(self.tank_size)
        elif isinstance(self.load_type, ParticleFactory):
            self.sample = self.load_type.createParticles(self.tank_size)

    def push(self):
        """
        Pushes particles to tank.

        """
        # normal push
        self.containersout.add(self.sample)


class TimingsDecision(CoreNode.Decision):
    def __init__(self, graph, readcontainers, time_thresh, gen_thresh, sample_size):
        """
        Checks if enough bonds have been formed/attempted in the generation and if not returns 0. Else checks if the
        number of generations has reached the threshold for termination, if not returns 1 else returns 2.
        0 - loop to bonding
        1 - loop to new generation
        2 - terminate simulation run

        Parameters
        ----------
        graph           :   nx.DiGraph
            graph the decision node will be added to.
        readcontainers  :   list<Containers>
            [Bonding Count Environment, Generation Count Environment, Main Tank]
        time_thresh     :   int
            Number of generations before termination
        gen_thresh      :   int
            Number of uses of the bonding node/subgraph in each generation.
        """
        super(TimingsDecision, self).__init__(graph, 3, readcontainers)
        self.time_thresh = time_thresh
        self.gen_thresh = gen_thresh
        self.sample_size = sample_size
        self.time = 0
        self.gen = 0
        self.tank_size = 0

    def read(self):
        """
        Reads in the current times and bond count.

        """
        self.gen = self.readcontainers[0].read()[0]
        self.time = self.readcontainers[1].read()[0]
        self.tank_size = len(self.readcontainers[2].read())

    def process(self):
        """
        Compares thresholds and current values and selects an option.

        Returns
        -------
        int
            Option of which control edge to take from this control node.

        """
        if self.gen < self.gen_thresh and self.tank_size >= self.sample_size:
            return 0
        elif self.time < self.time_thresh:
            return 1
        else:
            return 2


class TimeLoggerObserver(CoreNode.Observer):
    """
    Prints out a statement of the current generation and number of reactions.

    Parameters
    -----------
    rewrite :   Boolean
        Whether the print statement should start a new line or write over the previous one.
    """

    def __init__(self, graph, containersin, containersout, readcontainers=None, rewrite=True):
        super(TimeLoggerObserver, self).__init__(graph, containersin, containersout, readcontainers)
        self.rewrite = rewrite
        self.clock = 0
        self.reactions = 0
        self.clock_container = containersin[0]
        self.reactions_container = containersin[1]
        pass

    def read(self):
        """
        Reads in value of clock and reactions.

        """
        self.clock = self.clock_container.read()[0]
        self.reactions = self.reactions_container.read()[0]
        pass

    def pull(self):
        pass

    def process(self):
        """
        Prints out the current clock and reactions
        """
        print("Currently in generation: " + str(self.clock) + " and completed " + str(self.reactions) + " reactions.",
              end='\r')
        pass

    def push(self):
        pass


class SampleObserver(CoreNode.Observer):
    """
    Records the particles in a sample before a bond is attempted.
    """
    def __init__(self, graph, containersin, containersout, readcontainers=None):
        super(SampleObserver, self).__init__(graph, containersin, containersout, readcontainers)
        self.sample = None
        self.dict = None

    def read(self):
        self.sample = self.readcontainers.read()

    def pull(self):
        self.containersout.remove(["id1", "id2", "obj1", "obj2"])

    def process(self):
        if isinstance(self.sample[0], Particle):
            id1 = self.sample[0].id
        else:
            id1 = 'n/a'
        if len(self.sample)>1:
            if isinstance(self.sample[1], Particle):
                id2 = self.sample[1].id
            else:
                id2 = 'n/a'
            obj2 = self.sample[1]
        else:
            id2 = 'None'
            obj2 = 'None'
        obj1 = self.sample[0]
        self.dict = {"id1": id1, "id2": id2, "obj1": obj1, "obj2": obj2}

    def push(self):
        self.containersout.add(self.dict)

class ReturnObserver(CoreNode.Observer):
    def __init__(self, graph, containersin, containersout, VTime, readcontainers=None):
        super(ReturnObserver, self).__init__(graph, containersin, containersout, readcontainers)
        self.sample = None
        self.dict = None
        self.VGen = VTime  # Store the reference to VGen here

    def read(self):
        self.sample = self.readcontainers.read()
        self.dict = self.containersin.read()

    def process(self):
        current_generation = self.VGen.read()[0]  # Use the stored reference to read the current generation count
        if all(isinstance(x, RBNParticle) for x in self.sample):
            extended_prods = []
            open_spikes_info = []
            bonded_spikes_info = []
            intensities = []
            for part in self.sample:
                part_id = part.id
                open_spikes = [(spike[0].spikeNumber, spike[0].intensity) for spike in part.open_spikes]
                bonded_spikes = [(spike[0].spikeNumber, spike[0].intensity) for spike in part.bonds]
                total_intensity = sum(spike[1] for spike in open_spikes + bonded_spikes)

                part_info = f"RBNParticle(ID={part_id}, Atoms={len(part.atoms)}, Open={len(part.open_spikes)}, Bonded={len(part.bonds)})"
                extended_prods.append(part_info)
                open_spikes_info.append(open_spikes)
                bonded_spikes_info.append(bonded_spikes)
                intensities.append(total_intensity)

            self.dict["Prods"] = extended_prods
            self.dict["Open_Spikes"] = open_spikes_info
            self.dict["Bonded_Spikes"] = bonded_spikes_info
            self.dict["Intensity"] = intensities
            self.dict["Generation"] = current_generation
        else:
            self.dict["Prods"] = ["N/A"] * len(self.sample)
            self.dict["Open_Spikes"] = ["N/A"] * len(self.sample)
            self.dict["Bonded_Spikes"] = ["N/A"] * len(self.sample)
            self.dict["Intensity"] = ["N/A"] * len(self.sample)
            self.dict["Generation"] = current_generation

    def push(self):
        self.containersout.add(self.dict)


if __name__ == "__main__":
    for i in range(1):
        bond = RBNSpikeyWatsonBond()
        tank = WellMixedTank(bond, sample_size=2, load_type='RBN', generations=1000)
        sim = Simulate(tank.graph, tank.start, verbose=False)
        sim.run_graph(1000000000)
        tank.print_tank()
        tank.results_to_csv()
