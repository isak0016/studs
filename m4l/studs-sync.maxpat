{
	"patcher" : {
		"fileversion" : 1,
		"appversion" : {
			"major" : 8,
			"minor" : 5,
			"revision" : 5,
			"architecture" : "x64",
			"modernui" : 1
		},
		"classnamespace" : "box",
		"rect" : [ 85.0, 104.0, 700.0, 480.0 ],
		"bglocked" : 0,
		"openinpresentation" : 0,
		"default_fontsize" : 12.0,
		"default_fontface" : 0,
		"default_fontname" : "Arial",
		"boxes" : [
			{ "box" : { "id" : "btn-import", "maxclass" : "live.button", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "parameter_enable" : 1, "varname" : "import_button",
				"patching_rect" : [ 40.0, 40.0, 60.0, 20.0 ] } },
			{ "box" : { "id" : "lbl-import", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0,
				"text" : "choose project (once)", "patching_rect" : [ 40.0, 20.0, 160.0, 20.0 ] } },
			{ "box" : { "id" : "msg-import", "maxclass" : "message", "numinlets" : 2, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "import",
				"patching_rect" : [ 40.0, 70.0, 70.0, 20.0 ] } },

			{ "box" : { "id" : "route-import", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 2,
				"outlettype" : [ "", "" ], "text" : "route projectfolder",
				"patching_rect" : [ 40.0, 100.0, 150.0, 22.0 ] } },
			{ "box" : { "id" : "pattr-store", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "pattr project_folder",
				"patching_rect" : [ 40.0, 130.0, 150.0, 22.0 ] } },

			{ "box" : { "id" : "btn-push", "maxclass" : "live.button", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "parameter_enable" : 1, "varname" : "push_button",
				"patching_rect" : [ 260.0, 40.0, 60.0, 20.0 ] } },
			{ "box" : { "id" : "lbl-push", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0,
				"text" : "push", "patching_rect" : [ 260.0, 20.0, 60.0, 20.0 ] } },
			{ "box" : { "id" : "pattr-push", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "pattr project_folder",
				"patching_rect" : [ 260.0, 70.0, 150.0, 22.0 ] } },
			{ "box" : { "id" : "prepend-push", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "prepend sync push",
				"patching_rect" : [ 260.0, 100.0, 150.0, 22.0 ] } },

			{ "box" : { "id" : "btn-pull", "maxclass" : "live.button", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "parameter_enable" : 1, "varname" : "pull_button",
				"patching_rect" : [ 420.0, 40.0, 60.0, 20.0 ] } },
			{ "box" : { "id" : "lbl-pull", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0,
				"text" : "pull", "patching_rect" : [ 420.0, 20.0, 60.0, 20.0 ] } },
			{ "box" : { "id" : "pattr-pull", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "pattr project_folder",
				"patching_rect" : [ 420.0, 70.0, 150.0, 22.0 ] } },
			{ "box" : { "id" : "prepend-pull", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "prepend sync pull",
				"patching_rect" : [ 420.0, 100.0, 150.0, 22.0 ] } },

			{ "box" : { "id" : "btn-add", "maxclass" : "live.button", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "parameter_enable" : 1, "varname" : "add_project_button",
				"patching_rect" : [ 580.0, 40.0, 60.0, 20.0 ] } },
			{ "box" : { "id" : "lbl-add", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0,
				"text" : "add project", "patching_rect" : [ 580.0, 20.0, 100.0, 20.0 ] } },
			{ "box" : { "id" : "pattr-add", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "pattr project_folder",
				"patching_rect" : [ 580.0, 70.0, 150.0, 22.0 ] } },
			{ "box" : { "id" : "prepend-add", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "prepend newproject",
				"patching_rect" : [ 580.0, 100.0, 150.0, 22.0 ] } },

			{ "box" : { "id" : "node-script", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 2,
				"outlettype" : [ "", "" ], "text" : "node.script /Users/isakhaapaniemi/studs/studs/m4l/sync-bridge.js",
				"patching_rect" : [ 40.0, 220.0, 260.0, 22.0 ] } },
			{ "box" : { "id" : "node-debug", "maxclass" : "bpatcher", "name" : "n4m.monitor.maxpat",
				"numinlets" : 1, "numoutlets" : 0,
				"patching_rect" : [ 320.0, 220.0, 320.0, 220.0 ] } },
			{ "box" : { "id" : "msg-script-start", "maxclass" : "message", "numinlets" : 2, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "script start",
				"patching_rect" : [ 40.0, 180.0, 90.0, 20.0 ] } },

			{ "box" : { "id" : "prepend-set", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "prepend set",
				"patching_rect" : [ 40.0, 280.0, 100.0, 22.0 ] } },
			{ "box" : { "id" : "display", "maxclass" : "message", "numinlets" : 2, "numoutlets" : 1,
				"outlettype" : [ "" ], "text" : "(status appears here after a click)",
				"patching_rect" : [ 40.0, 330.0, 360.0, 40.0 ] } }
		],
		"lines" : [
			{ "patchline" : { "source" : [ "btn-import", 0 ], "destination" : [ "msg-import", 0 ] } },
			{ "patchline" : { "source" : [ "msg-import", 0 ], "destination" : [ "node-script", 0 ] } },

			{ "patchline" : { "source" : [ "btn-push", 0 ], "destination" : [ "pattr-push", 0 ] } },
			{ "patchline" : { "source" : [ "pattr-push", 0 ], "destination" : [ "prepend-push", 0 ] } },
			{ "patchline" : { "source" : [ "prepend-push", 0 ], "destination" : [ "node-script", 0 ] } },

			{ "patchline" : { "source" : [ "btn-pull", 0 ], "destination" : [ "pattr-pull", 0 ] } },
			{ "patchline" : { "source" : [ "pattr-pull", 0 ], "destination" : [ "prepend-pull", 0 ] } },
			{ "patchline" : { "source" : [ "prepend-pull", 0 ], "destination" : [ "node-script", 0 ] } },

			{ "patchline" : { "source" : [ "btn-add", 0 ], "destination" : [ "pattr-add", 0 ] } },
			{ "patchline" : { "source" : [ "pattr-add", 0 ], "destination" : [ "prepend-add", 0 ] } },
			{ "patchline" : { "source" : [ "prepend-add", 0 ], "destination" : [ "node-script", 0 ] } },

			{ "patchline" : { "source" : [ "msg-script-start", 0 ], "destination" : [ "node-script", 0 ] } },

			{ "patchline" : { "source" : [ "node-script", 0 ], "destination" : [ "route-import", 0 ] } },
			{ "patchline" : { "source" : [ "route-import", 0 ], "destination" : [ "pattr-store", 0 ] } },
			{ "patchline" : { "source" : [ "route-import", 1 ], "destination" : [ "prepend-set", 0 ] } },
			{ "patchline" : { "source" : [ "prepend-set", 0 ], "destination" : [ "display", 0 ] } },
			{ "patchline" : { "source" : [ "node-script", 1 ], "destination" : [ "node-debug", 0 ] } }
		]
	}
}
