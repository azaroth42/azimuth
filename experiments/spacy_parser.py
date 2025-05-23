import spacy
from bagpipes_spacy import PhrasesExtractor  # noqa

parser = spacy.load("en_core_web_sm")
parser.add_pipe("phrases_extractor")

argstr = "carefully look at my big bread with the little telescope here"
argstr = "look at myself"
cmd = parser(argstr)
verbs = list(cmd._.verb_phrases)
if not verbs:
    cmd = parser(f"I {argstr}")
    verbs = list(cmd._.verb_phrases)
    if not verbs:
        print(f"Failed to parse: {argstr}")

# look for registered commands
verb = verbs[0].root.text
possible = []

basic = []
for token in cmd:
    basic.append(f"{token.text}/{token.pos_}-{token.dep_}")
print(" ".join(basic))

objects = list(cmd._.noun_phrases)
preps = list(cmd._.prep_phrases)

for v in verbs:
    c = v.root
    if c.dep_ in ["advcl", "acl", "xcomp"]:
        print(f"found sub-clause ({c}), continuing")
        continue
    info = {"cmd": c}
    for np in objects:
        for ch in np.noun_chunks:
            if ch.root == np.root:
                np = ch
                break
        ancs = list(np.root.ancestors)
        if ancs[-1] == v.root:
            # attached to this verb
            which = np.root.dep_
            if which == "pobj":
                which = "iobj"
            if len(ancs) > 1:
                # prepositions?
                try:
                    info["prep"].append(ancs[0])
                except:
                    info["prep"] = [ancs[0]]
            try:
                info[which].append(np)
            except Exception as e:
                info[which] = [np]
    print(info)



else:
    cmd = parser(argstr)
    verbs = list(cmd._.verb_phrases)
    if not verbs:
        cmd = parser(f"I {argstr}")
        verbs = list(cmd._.verb_phrases)
        if not verbs:
            print(f"Failed to parse: {argstr}")
            player.tell("I don't understand that")
            return

    # look for registered commands
    verb = verbs[0].root.text

    basic = []
    for token in cmd:
        basic.append(f"{token.text}/{token.pos_}-{token.dep_}")
    print(" ".join(basic))

    objects = list(cmd._.noun_phrases)
    for v in verbs:
        c = v.root
        if c.dep_ in ["advcl", "acl", "xcomp"]:
            print(f"found sub-clause ({c}), continuing")
            continue
        info = {"verb": c, "prep": [], "dobj": [], "iobj": []}
        for np in objects:
            for ch in np.noun_chunks:
                if ch.root == np.root:
                    np = ch
                    break
            ancs = list(np.root.ancestors)
            if ancs[-1] == v.root:
                # attached to this verb
                which = np.root.dep_
                if which == "pobj":
                    which = "iobj"
                if len(ancs) > 1:
                    # prepositions?
                    info["prep"].append(ancs[0])
                try:
                    info[which].append(np)
                except:
                    # nsubj, conj, etc
                    info[which] = [np]
        print(info)
        break

    possible_matches = [] # Store tuples of (source_object, command_option, resolved_arguments)
    verb = info["verb"].root.text # Using the lemma or base form of the verb from parsing results

    for s in search_order:
        cmds = s.get_commands()
        if verb in cmds:
            for opt in cmds[verb]:
                # --- Check structural compatibility based on prepositions ---
                parsed_prep_text = info["prep"][0].text if info["prep"] else None
                opt_preps = opt.get("prep") # opt["prep"] is None or a list of strings

                prep_match = False
                if opt_preps is None and parsed_prep_text is None:
                    prep_match = True # Command requires no prep, parser found no prep
                elif opt_preps is not None and parsed_prep_text is not None and parsed_prep_text in opt_preps:
                    prep_match = True # Command requires prep, parser found prep and it matches

                if not prep_match:
                    continue # Skip this command option if prepositions don't match

                # --- Attempt to resolve objects if structure is plausible ---
                resolved_args = {}
                resolution_failed = False

                # Resolve Direct Objects (if any were parsed)
                if info['dobj']:
                    # Assuming the command option implies a dobj is possible if preps match or no preps required
                    parsed_dobj_text = info['dobj'][0].text # Assuming one dobj for now
                    matches = s.match_object(parsed_dobj_text)
                    if matches:
                        resolved_args['dobj'] = matches # Store the list of potential matches
                    else:
                        resolution_failed = True # Failed to resolve a parsed dobj

                # Resolve Indirect Objects (if any were parsed)
                if not resolution_failed and info['iobj']:
                    # Assuming the command option implies an iobj is possible if preps match
                    parsed_iobj_text = info['iobj'][0].text # Assuming one iobj for now
                    matches = s.match_object(parsed_iobj_text)
                    if matches:
                        resolved_args['iobj'] = matches # Store the list of potential matches
                    else:
                        resolution_failed = True # Failed to resolve a parsed iobj

                # If resolution was successful for all parsed objects mentioned in input:
                if not resolution_failed:
                    # Found a plausible command option and resolved the mentioned objects
                    possible_matches.append({"source": s, "option": opt, "resolved": resolved_args})

    print(possible_matches) # Debugging print

    # --- Process the possible_matches ---
    if len(possible_matches) == 1:
        match = possible_matches[0]
        source = match["source"]
        option = match["option"]
        resolved_args = match["resolved"]

        try:
            # Pass resolved objects as keyword arguments. Command functions need to accept them.
            option['func'](source, player, **resolved_args)
        except Exception as e:
             player.tell(f"An error occurred while trying to execute that command: {e}")
             # Log the error server-side for debugging
             print(f"Error executing command {option.get('verb', 'unknown')} for player {player.name}: {e}")


    elif len(possible_matches) > 1:
        # Ambiguity - multiple possible commands matched
        player.tell("I'm not sure what you mean. Multiple actions are possible with that command.")
        # TODO: Enhance ambiguity reporting to list potential commands/sources.


    else: # len(possible_matches) == 0
        # No command matched the parsed structure and resolved objects
        player.tell("I don't understand that.") # Use the existing message
