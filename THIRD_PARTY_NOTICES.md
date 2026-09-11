# Third-party assets

ROVE's MIT license does not relicense bundled datasets, robot descriptions, or
meshes. Keep this notice and the supplied license text when redistributing them.

| Local files | Upstream source | License and modifications |
| --- | --- | --- |
| `data/{assembly,bin-picking,drawer-cabinet,kitchen,tabletop}/robo2vlm_*.jpg`, associated QA | [keplerccc/Robo2VLM-1](https://huggingface.co/datasets/keplerccc/Robo2VLM-1) | Dataset card: Apache-2.0. Selected frames; task text curated for the demo. Source IDs are recorded in the manifest/QA files. |
| `data/libero/*.jpg`, `data/libero_goal/*.jpg`, associated metadata | [lerobot/libero_10_image](https://huggingface.co/datasets/lerobot/libero_10_image) and [lerobot/libero_goal_image](https://huggingface.co/datasets/lerobot/libero_goal_image) | Dataset card: Apache-2.0. Selected/re-encoded frames and locally curated tasks/goal comparisons. Earlier manifest MIT labels have been corrected. These assets are not newly captured robot episodes. |
| `data/urdf/panda/panda.urdf`, `data/urdf/panda/meshes/collision/*.stl` | [MoveIt panda_description](https://github.com/moveit/moveit_resources/tree/11e8083fe4d741ce222f645fd570b77b4bdf9ff8/panda_description) | The directory's LICENSE is Apache-2.0 (despite a BSD label in package metadata). All ten collision meshes match this revision byte-for-byte. The URDF changes package URLs to local mesh paths; visual meshes are not distributed. |

The upstream Panda LICENSE is copied verbatim to
[licenses/Apache-2.0.txt](licenses/Apache-2.0.txt). It is also the applicable license
text for the dataset cards listed above. Original copyrights remain with their
respective authors. No separate upstream NOTICE file was present in the inspected
Panda directory. Dataset cards and source IDs remain the reference for original
data contributors and citation instructions.

The dashboard currently loads Tailwind, Lucide and Google Fonts from third-party
CDNs; these assets are not bundled or covered by ROVE's MIT grant. See SECURITY.md
for the resulting network/data-handling limitations.
