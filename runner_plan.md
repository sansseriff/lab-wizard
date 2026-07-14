You will notice that this repo/tool 'lab wizard' has a way of creating projects that are pre-written using a GUI that works to load instruments, database, and plotting systems using a yaml config tree for setting and parameter handling. 

Right now, the GUI does not support directly running of created projects after they have been created by workflows like the "create measurement" workflow. 

I would like to extend the GUI and backend systems to support running measurements. Either through a new button on the home page (like "View & Run a Project"), or as a final step in the Create Measurement flow. 

Notice that there are some bits of 'plotters' which are systems that take data emitted by a measurement and plot it somewhere. The idea is to have a local matplotlib plotter, and a web based plotter that uses the bokeh library underneath (see this repo for context about how to use bokeh as purely a frontend plotting library. I choose it because its a low setup high performance plotting library that uses webgl canvas: /Users/andrew/Documents/PROGRAM_LOCAL/tag_gui) 

Given all this, I would like to create a new webpage that can be accesible from (1) the main page following a "pick measurements" page and (2) at the end of the "create measurement". Keep in mind, for (1) you will need to make a new webpage that scans/scrapes the folders in the projects directory