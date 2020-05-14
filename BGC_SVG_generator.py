#!/usr/bin/env python

"""
This script will use the BGC library to predict protein domains of a GenBank
file and create svg figures of the whole cluster as well as for every protein
"""

import os
import argparse
from multiprocessing import cpu_count
import pickle
from BGClib import *

__author__ = "Jorge Navarro"
__version__ = "1"
__maintainer__ = "Jorge Navarro"
__email__ = "j.navarro@wi.knaw.nl"


# Prepare arguments for the script
def CMD_parser():
    parser = argparse.ArgumentParser(description="BGC SVG generator.\
        Generate SVG figures from GenBank files or serialized .bgc objects \
        [v{}]".format(__version__))
    
    group_input = parser.add_argument_group("Input")
    
    group_input.add_argument("-i", "--inputfolders", nargs='+', help="Folder(s)\
        to look for .gb and .gbk files (note: output folder will not preserve \
        the structure of input folders).")
    group_input.add_argument("-f", "--files", nargs='+', help="File(s) used to \
        draw the figures (accepted: .gb .gbk, .bgc, .bgccase). Note: for\
        .bgc and .bgccase files, inclusion rules by --include, --exclude\
        and --bgclist will be applied to the internal BGC identifier, not the\
        name of the file")
    group_input.add_argument("--hmm", nargs='+', help="Location of .hmm file(s).\
        This will also enable internal hmm models. Note that if the SVG style \
        options have 'draw_domains=False', no domain prediction will be made, \
        even if .hmm files are specified")
    group_input.add_argument("-l", "--bgclist", help="A file containing a list \
        of BGC identifiers (i.e. filename without .gb or .gbk extension).\
        If specified, use it to filter all the BGCs found with --inputfolders \
        or --files. \
        If --stacked is used, this list will determine the order (and filename).\
        An optional second column (separated by a tab) with the Protein ID can\
        be specified. If this column is present, the BGC will be mirrored if \
        needed such that the gene encoding the Protein ID in the second column\
        is in the forward strand. Additionally, if --stacked is used and all \
        Protein IDs are present, the corresponding gene will also be used for \
        horizontal alignment.\
        Any extra columns or rows starting with the '#' character will be \
        ignored.\
        The BGC identifiers in this file are treated case-sensitive.",
        type=Path)
    group_input.add_argument("--include", nargs='*', default=['region', 'cluster'], 
        help="Specify string(s) to filter which BGCs will be accepted. In the \
        case of .gb or .gbk files, the filter is applied to the filename. For \
        data stored as .bgc or .bgccase files, the filter is applied to the \
        BGC(s) identifier. If the argument is present but no parameters are \
        given, the filter will be ignored. If the argument is not present, \
        the default is to use the strings 'region' and 'cluster')")
    group_input.add_argument("--exclude", nargs='*', default=['final'], 
        help="Specify string(s) to filter which BGCs will be rejected. \
        Similar rules are applied as with --include. If the argument is not \
        present, the default is to use 'final'.")
    
    group_processing = parser.add_argument_group("Processing options")
    
    group_processing.add_argument("--cfg", 
        default=(Path(__file__).parent/"SVG_arrow_options.cfg"),
        help="Configuration file with SVG style. Default: \
        'SVG_arrow_options.cfg'")
    group_processing.add_argument("-m", "--mirror", default=False, 
        action="store_true", help="Toggle to mirror each BGC figure. Ignored \
        with --stacked or --bgclist")
    group_processing.add_argument("--override", help="Use domain prediction in \
        .bgc and .bgccase files, even if they already contain domain \
        data (does not overwrite input files).", default=False, 
        action="store_true")
    group_processing.add_argument("-c", "--cpus", type=int, default=cpu_count(), 
        help="Number of CPUs used for domain prdiction. Default: all available")
    
    group_output = parser.add_argument_group("Output")
    
    group_output.add_argument("-o", "--outputfolder", 
        default=(Path(__file__).parent/"output"), help="Folder where results \
        will be put (default='output')")
    group_output.add_argument("-s", "--stacked", default=False, 
        action="store_true", help="If used, all BGCs will be put in the same \
        figure. Default: each BGC has its own SVG.")
    group_output.add_argument("-g", "--gaps", default=False, action="store_true",
        help="If --stacked is used, toggle this option to leave gaps\
        when a particular BGC is not found in the input data")
    
    return parser.parse_args()


def check_input_data(inputfiles, inputfolders, hmms, bgclist):
    """
    Checkes whether all paths to files and folders are valid
    """
    
    if not inputfiles and not inputfolders:
        sys.exit("Error: no input data. See options using -h")
        
    if inputfiles:
        for file_ in inputfiles:
            if not Path(file_).is_file():
                sys.exit("Error (--files): {} is not a file".format(file_))
                
    if inputfolders:
        for folder in inputfolders:
            if not Path(folder).is_dir():
                sys.exit("Error (--inputfolders): {} is not a folder".format(folder))
            
    if hmms:
        for hmm in hmms:
            hmm_file = Path(hmm)
            if not hmm_file.is_file():
                sys.exit("Error (--hmm): {} is not a file".format(hmm_file))
            if hmm_file.suffix not in {".hmm", ".HMM"}:
                sys.exit("Error (--hmm): {} does not have a .hmm extension".format(hmm_file))

    if bgclist:
        if not Path(bgclist).is_file():
            sys.exit("Error: (--bgclist): {} is not a file".format(bgclist))


def valid_name(name, include, exclude, filter_bgc):
    """
    Checks whether a filename is valid and should be included in the analysis
    based on the allowed strings (args.include) or strings to be avoided
    (args.exclude) as well as the criterium that the BGC is included in the
    filter list.
    It is expected that the parameter 'name' is the name of the file \
    without extension
    """

    if len(include) > 0:
        if not any([word in name for word in include]):
            return False
        
    if len(exclude) > 0:
        if exclude != [] and any(word in name for word in exclude):
            return False
    
    if len(filter_bgc) > 0:
        if name not in filter_bgc:
            return False
    
    return True


def get_bgc_files(inputfolders, files, include, exclude, filter_bgc):
    """
    Reads various data sources, applies filters (strings, list)
    """
    
    input_bgc_files = dict() # Keeps a record of location of files
    collection_working = BGCCollection() # BGCs that need domain prediction
    collection_external = BGCCollection() # From .bgc or .bgccase files
                                        # They may not need domain prediction
    
    if inputfolders:
        for x in inputfolders:
            inputfolder = Path(x)
            for gb_file in inputfolder.glob("**/*.gb"):
                if valid_name(gb_file.stem, include, exclude, filter_bgc):
                    input_bgc_files[gb_file.stem] = gb_file
                    collection_working.bgcs[gb_file.stem] = BGC(gb_file)
            
            for gbk_file in inputfolder.glob("**/*.gbk"):
                if valid_name(gbk_file.stem, include, exclude, filter_bgc):
                    # If we have duplicate cluster names, use the .gbk
                    if gbk_file.stem in input_bgc_files:
                        print("Warning: substituting {} \n\twith {}".format(
                            input_bgc_files[gbk_file.stem], gbk_file))
                        input_bgc_files[gbk_file.stem] = gbk_file
                    collection_working.bgcs[gbk_file.stem] = BGC(gbk_file)
    
    if files:
        for str_file in files:
            f = Path(str_file)
            if f.suffix.lower() in {".gb", ".gbk"}:
                if valid_name(f.stem, include, exclude, filter_bgc):
                    if f.stem in input_bgc_files:
                        print("Warning: substituting {} \n\twith {}".format(input_bgc_files[f.stem], f))
                    input_bgc_files[f.stem] = f
                    collection_working.bgcs[f.stem] = BGC(f)
                    
            elif f.suffix.lower() == ".bgc":
                with open(f, "rb") as dc:
                    bgc = pickle.load(dc)
                    bgc_id = bgc.identifier
                if valid_name(bgc_id, include, exclude, filter_bgc):
                    if bgc_id in input_bgc_files:
                        print("Warning: substituting {} \n\twith {}\
                                ".format(input_bgc_files[bgc_id], f))
                        if not override:
                            del collection_working[bgc_id]
                    
                    if override:
                        # flag this bgc to re-predict domains
                        collection_working.bgcs[bgc_id] = bgc
                    else:
                        collection_external.bgcs[bgc_id] = bgc
                    
                    input_bgc_files[bgc_id] = f
                        
            elif f.suffix.lower() == ".bgccase":
                with shelve.open(f, flag='r') as col:
                    # if we've got a filter list, use it
                    if len(filter_bgc) > 0:
                        for bgc_id in filter_bgc:
                            try:
                                bgc = col[bgc_id]
                            except KeyError:
                                continue
                            
                            if valid_name(bgc_id, include, exclude, \
                                    filter_bgc):
                                if bgc_id in input_bgc_files:
                                    print("Warning: substituting {} \n\t with \
                                          bgc in collection {}".format(\
                                              input_bgc_files[bgc_id], f))
                                    if not override:
                                        del collection_working[bgc_id]
                                
                                if override:
                                    collection_working.bgcs[bgc_id] = bgc
                                else:
                                    collection_external.bgcs[bgc_id] = bgc
                    # no filter list. Check all content in the collection
                    else:
                        for bgc_id in col:
                            bgc = col[bgc_id]
                            if valid_name(bgc_id, include, exclude, \
                                filter_bgc):
                                if bgc_id in input_bgc_files:
                                    print("Warning: substituting {} \n\t with \
                                            bgc in collection {}".format(\
                                                input_bgc_files[bgc_id], f))
                                    if not override:
                                        del collection_working[bgc_id]
                                
                                if override:
                                    collection_working.bgcs[bgc_id] = bgc
                                else:
                                    collection_external.bgcs[bgc_id] = bgc
            else:
                print("Warning: unknown format ({})".format(f))

    return collection_working, collection_external


def draw_svg_individual(o, svg_collection, svgopts, hmmdbs, mirror, filter_bgc):
    for bgc_id in svg_collection.bgcs:
        bgc = svg_collection.bgcs[bgc_id]
        
        m = mirror
        # see if we need to mirror the cluster
        if bgc_id in filter_bgc:
            pid = filter_bgc[bgc_id]
            
            if pid != "":
                for p in bgc.protein_list:
                    if pid == p.protein_id or pid == p.identifier:
                        if not p.forward:
                            m = True
                            break
        
        coregenearch = ""
        if len(bgc.CBPtypes) > 0:
            coregenearch = "_[{}]".format(",".join(bgc.CBPtypes))
                
        m_info = ""
        if m:
            m_info = "_m"
            
        bgc_name = o / "{}{}{}.svg".format(bgc_id, coregenearch, m_info)
        bgc.BGC_SVG(bgc_name, hmmdbs, svgopts, mirror=m)


def draw_svg_stacked(filename, svg_collection, svgopts, hmmdbs, gaps, filter_bgc, filter_bgc_order):
    """
    Draws a stacked BGC figure
    """
    thickness = svgopts.gene_contour_thickness
    
    # Obtain a final list of bgc_ids in the order that they must be printed
    if len(filter_bgc_order) > 0:
        # provided list of BGCs. Not all of them were necessarily found
        draw_order = filter_bgc_order
    else:
        # No list provided. Include everything in the order it was read
        draw_order = [*svg_collection.bgcs] # convert iterable dict's keys into list
    
    # Now obtain a list of protein objects that will be used for alignment
    # Also get information about mirroring and offset distancing
    scaling = svgopts.scaling
    H = svgopts.arrow_height # used for loci spacing
    needs_mirroring = dict()
    bgc_lengths = dict()
    bgc_distance_to_target = dict()
    target_to_start_max_offset = 0
    for bgc_id in draw_order:
        try:
            bgc = svg_collection.bgcs[bgc_id]
        except KeyError:
            print(" Warning: Cannot find BGC {} in input data".format(bgc_id))
            continue
        
        # second term in the addition: inter-loci spacing element
        L = sum([locus.length/scaling for locus in bgc.loci]) \
            + H * (len(bgc.loci)-1) \
            + thickness
        bgc_lengths[bgc_id] = L
        
        if bgc_id in filter_bgc:
            if filter_bgc[bgc_id] == "":
                print(" Warning (--bgclist): {} has not reference Protein Id".format(bgc_id))
                needs_mirroring[bgc_id] = False
                bgc_distance_to_target[bgc_id] = -1
            else:
                # try to find protein with given protein_id
                pid = filter_bgc[bgc_id]
                target_protein = None
                for locus_num in range(len(bgc.loci)):
                    locus = bgc.loci[locus_num]
                    for protein in locus.protein_list:
                        if pid == protein.protein_id or pid == protein.identifier:
                            needs_mirroring[bgc_id] = not protein.forward
                            target_protein = protein
                            if protein.forward:
                                # lenghts of each loci until target + 
                                # inter-loci spacing + distance of current \
                                # locus to target_protein
                                target_to_start = \
                                    sum([locus.length/scaling for locus in bgc.loci[:locus_num]]) \
                                    + H * locus_num \
                                    + protein.cds_regions[0][0]/scaling 
                                bgc_distance_to_target[bgc_id] = target_to_start
                            else:
                                target_to_start = \
                                    sum([locus.length/scaling for locus in bgc.loci[locus_num+1:]]) \
                                    + H * (len(bgc.loci) - locus_num - 1) \
                                    + (locus.length - protein.cds_regions[-1][1])/scaling
                                bgc_distance_to_target[bgc_id] = target_to_start
                                
                            if target_to_start > target_to_start_max_offset:
                                target_to_start_max_offset = target_to_start
                            break
                        
                # typo in Protein Id?
                if target_protein == None:
                    print(" Warning (--bgclist): cannot find reference Protein Id {} for {}".format(pid, bgc_id))
                    needs_mirroring[bgc_id] = False
                    bgc_distance_to_target[bgc_id] = -1
        else:
            needs_mirroring[bgc_id] = False
            bgc_distance_to_target[bgc_id] = -1
    
    # obtain max_L considering all the starting offsets
    max_L = 0
    for bgc_id in bgc_distance_to_target:
        if bgc_distance_to_target[bgc_id] == -1:
            max_L = max(max_L, bgc_lengths[bgc_id])
        else:
            max_L = max(max_L, bgc_lengths[bgc_id] \
                + target_to_start_max_offset \
                - bgc_distance_to_target[bgc_id])

    # Start SVG internal structure
    bgc_height = 2*svgopts.arrow_height # one for the arrow, 0.5 + 0.5 for the head height
    inner_bgc_height = bgc_height + thickness
    base_attribs = {"version":"1.1", 
                    "baseProfile":"full", 
                    "width":str(int(max_L))
                    }
    root = etree.Element("svg", attrib=base_attribs, nsmap={None:'http://www.w3.org/2000/svg'})
    
    # Add each figure
    Yoffset = 0
    rows = 0
    for bgc_id in draw_order:
        Yoffset = rows * inner_bgc_height
        try:
            bgc = svg_collection.bgcs[bgc_id]
        except KeyError:
            if gaps:
                rows += 1
            continue
        
        # Marked BGCs with no reference Protein Id won't have offset
        if bgc_distance_to_target[bgc_id] == -1:
            Xoffset = 0
        else:
            Xoffset = target_to_start_max_offset - bgc_distance_to_target[bgc_id]
        root.append(bgc.xml_BGC(Xoffset, Yoffset, hmmdbs, svgopts, needs_mirroring[bgc_id]))
        rows += 1
            
        #Yoffset = rows * inner_bgc_height
    
    # Now that we now how many BGCs were actually drawn, add height property
    root.attrib["height"] = str(int(thickness + rows*(bgc_height+thickness)))
    
    # Write SVG
    with open(filename, "bw") as f:
        f.write(etree.tostring(root, pretty_print=True))
        

if __name__ == "__main__":
    args = CMD_parser()

    # Verify user typed paths correctly
    check_input_data(args.files, args.inputfolders, args.hmm, args.bgclist)
    
    # Get parameters
    stacked = args.stacked
    outputfolder = args.outputfolder
    override = args.override
    mirror = args.mirror
    gaps = args.gaps
    
    # Read filter list
    filter_bgc = dict()
    filter_bgc_order = list() # Dictionaries should keep order in Python
                            # 3.something but let's make sure
    if args.bgclist:
        with open(args.bgclist) as f:
            for line in f:
                if line[0] == "#":
                    continue
                
                xline = line.strip().split("\t")
                filter_bgc_order.append(xline[0])
                if len(xline) > 1:
                    # assume second column is protein_id
                    filter_bgc[xline[0]] = xline[1]
                else:
                    filter_bgc[xline[0]] = ""
        
        if len(filter_bgc) == 0:
            sys.exit("Error: filter BGC list given but the file is empty...")

    # Style options
    svgopts = ArrowerOpts(args.cfg)
    
    # Add hmm databases
    hmmdbs = HMM_DB()
    if svgopts.draw_domains and args.hmm:
        hmmdbs = HMM_DB()
        hmmdbs.cores = args.cpus
        hmmdbs.add_included_database()
        
        for hmm in args.hmm:
            hmmdbs.add_database(Path(hmm))
        
    # Read input data:
    print("Collecting data")
    if len(args.include) > 0:
        print(" - Including only BGCs with the following:")
        print("\t{}".format("\n\t".join(args.include)))
    if len(args.exclude) > 0:
        print(" - Excluding all BGCs with the following:")
        print("\t{}".format("\n\t".join(args.exclude)))
    print("")
    collection_working, collection_external = get_bgc_files(args.inputfolders, 
                                                            args.files, 
                                                            args.include, 
                                                            args.exclude, 
                                                            filter_bgc)
    total_bgcs = len(collection_working.bgcs) + len(collection_external.bgcs)
    if total_bgcs == 0:
        sys.exit("No valid files were found")
    else:
        print("Working with {} BGC(s)".format(total_bgcs))
    
    #  Output folder
    if args.outputfolder:
        o = Path(args.outputfolder)
        if not o.is_dir():
            print("Trying to create output folder")
            os.makedirs(o, exist_ok=True) # recursive folder creation
    else:
        # There is a default value for this parameter so we should actually not
        # have this case...
        o = (Path(__file__).parent / "output")
        if not o.is_dir():
            os.makedirs(o, exist_ok=True)
            
    # Ready to start
    if svgopts.draw_domains and args.hmm:
        print("Predicting domains...")
        collection_working.predict_domains(hmmdbs, cpus=hmmdbs.cores)
        print("\tdone!")
    
    svg_collection = BGCCollection() # BGCs that will be rendered
    svg_collection.bgcs.update(collection_working.bgcs)
    svg_collection.bgcs.update(collection_external.bgcs)
    svg_collection.classify_proteins(args.cpus)
    
    if stacked:
        print("Generating stacked figure")
        if args.bgclist:
            filename = o / "{}.svg".format(args.bgclist.stem)
        else:
            filename = o / "stacked_BGC_figure.svg"
        draw_svg_stacked(filename, svg_collection, svgopts, hmmdbs, gaps, filter_bgc, filter_bgc_order)
    else:
        print("Generating individual figures")
        draw_svg_individual(o, svg_collection, svgopts, hmmdbs, mirror, filter_bgc)
        

